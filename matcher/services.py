"""Бизнес-логика: сохранение анкет, генерация текста, лимиты и журнал."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from . import ai
from .models import ActionLog, BotConfig, GeneratedMessage, Profile

logger = logging.getLogger(__name__)

DEDUP_WINDOW_DAYS = 45


def log_event(event: str, detail: str = "", level: str = ActionLog.LEVEL_INFO, profile=None) -> None:
    ActionLog.objects.create(event=event, detail=detail[:4000], level=level, profile=profile)
    logger.info("%s: %s", event, detail[:300])


def sent_today() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return GeneratedMessage.objects.filter(
        status=GeneratedMessage.STATUS_SENT, sent_at__gte=start
    ).count()


def can_run(config: BotConfig | None = None) -> tuple[bool, str]:
    """Можно ли сейчас обрабатывать анкеты."""
    config = config or BotConfig.get_solo()
    if not config.enabled:
        return False, "бот выключен в настройках"
    if config.is_paused:
        return False, f"пауза до {timezone.localtime(config.paused_until):%H:%M %d.%m}"
    used = sent_today()
    if used >= config.daily_limit:
        return False, f"достигнут дневной лимит ({used}/{config.daily_limit})"
    return True, ""


def passes_filters(data: dict, config: BotConfig | None = None) -> tuple[bool, str]:
    config = config or BotConfig.get_solo()
    age = data.get("age")
    if age is not None and not (config.min_age <= age <= config.max_age):
        return False, f"возраст {age} вне диапазона {config.min_age}–{config.max_age}"
    city_filter = (config.city_filter or "").strip().lower()
    if city_filter and city_filter not in (data.get("city") or "").lower():
        return False, f"город «{data.get('city')}» не подходит под фильтр «{config.city_filter}»"
    return True, ""


def find_duplicate(fingerprint: str) -> Profile | None:
    since = timezone.now() - timedelta(days=DEDUP_WINDOW_DAYS)
    return (
        Profile.objects.filter(fingerprint=fingerprint, created_at__gte=since)
        .exclude(status=Profile.STATUS_FAILED)
        .order_by("-created_at")
        .first()
    )


def save_profile(
    data: dict,
    *,
    chat_id: int,
    message_id: int = 0,
    grouped_id: int | None = None,
    photos: list[str] | None = None,
) -> Profile:
    fingerprint = Profile.make_fingerprint(
        data.get("name", ""), data.get("age"), data.get("city", ""), data.get("about", "")
    )
    return Profile.objects.create(
        chat_id=chat_id,
        message_id=message_id,
        grouped_id=grouped_id,
        name=data.get("name", "") or "",
        age=data.get("age"),
        city=data.get("city", "") or "",
        about=data.get("about", "") or "",
        raw_caption=data.get("raw_caption", "") or "",
        language=data.get("language", "") or "",
        photos=photos or [],
        fingerprint=fingerprint,
    )


def generate_for_profile(profile: Profile, config: BotConfig | None = None) -> GeneratedMessage:
    """Генерирует и сохраняет черновик сообщения для анкеты."""
    config = config or BotConfig.get_solo()
    try:
        result = ai.generate_reply(
            name=profile.name,
            age=profile.age,
            city=profile.city,
            about=profile.about,
            tone=config.tone,
            max_chars=config.max_chars,
            extra_instructions=config.extra_instructions,
            photos=profile.photos,
            use_vision=config.use_vision,
            forced_language=config.language,
        )
    except ai.AIError as exc:
        profile.status = Profile.STATUS_FAILED
        profile.skip_reason = str(exc)[:255]
        profile.save(update_fields=["status", "skip_reason"])
        log_event("generate_failed", str(exc), ActionLog.LEVEL_ERROR, profile)
        raise

    message = GeneratedMessage.objects.create(
        profile=profile,
        text=result["text"],
        language=result["language"],
        strategy=result["strategy"],
        model=result["model"],
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
    )
    if profile.status == Profile.STATUS_NEW:
        profile.status = Profile.STATUS_GENERATED
        profile.save(update_fields=["status"])
    log_event("generated", f"[{result['strategy']}/{result['language']}] {result['text']}", profile=profile)
    return message


def mark_sent(message: GeneratedMessage) -> None:
    message.status = GeneratedMessage.STATUS_SENT
    message.sent_at = timezone.now()
    message.error = ""
    message.save(update_fields=["status", "sent_at", "error"])
    Profile.objects.filter(pk=message.profile_id).update(status=Profile.STATUS_SENT)
    log_event("sent", message.text, profile=message.profile)


def mark_failed(message: GeneratedMessage, error: str) -> None:
    message.status = GeneratedMessage.STATUS_FAILED
    message.error = error[:2000]
    message.save(update_fields=["status", "error"])
    Profile.objects.filter(pk=message.profile_id).update(status=Profile.STATUS_FAILED)
    log_event("send_failed", error, ActionLog.LEVEL_ERROR, message.profile)


def mark_skipped(profile: Profile, reason: str) -> None:
    profile.status = Profile.STATUS_SKIPPED
    profile.skip_reason = reason[:255]
    profile.save(update_fields=["status", "skip_reason"])
    log_event("skipped", reason, ActionLog.LEVEL_WARNING, profile)


def stats() -> dict:
    config = BotConfig.get_solo()
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "enabled": config.enabled,
        "paused_until": config.paused_until,
        "daily_limit": config.daily_limit,
        "sent_today": sent_today(),
        "profiles_total": Profile.objects.count(),
        "profiles_today": Profile.objects.filter(created_at__gte=today_start).count(),
        "messages_total": GeneratedMessage.objects.count(),
        "messages_sent": GeneratedMessage.objects.filter(
            status=GeneratedMessage.STATUS_SENT
        ).count(),
        "with_description": Profile.objects.exclude(about="").count(),
        "without_description": Profile.objects.filter(about="").count(),
        "failed": Profile.objects.filter(status=Profile.STATUS_FAILED).count(),
    }
