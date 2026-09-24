"""Обёртка над OpenAI: превращает анкету в готовое первое сообщение."""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
import time
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from .parser import detect_language
from .prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

MAX_VISION_PHOTOS = 2


class AIError(RuntimeError):
    """Не удалось получить текст от OpenAI."""


@lru_cache(maxsize=1)
def _client():
    if not settings.OPENAI_API_KEY:
        raise AIError("OPENAI_API_KEY не задан — заполните .env")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise AIError("Пакет openai не установлен: pip install -r requirements.txt") from exc
    return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT)


MAX_OUTPUT_TOKENS = 400

# Разные семейства моделей принимают разные параметры: старые — max_tokens,
# новые (o-серия, gpt-5) — max_completion_tokens и только temperature=1.
_PARAM_FIXES = {
    "max_tokens": lambda params: params.update(
        {"max_completion_tokens": params.pop("max_tokens", MAX_OUTPUT_TOKENS)}
    ),
    "max_completion_tokens": lambda params: params.update(
        {"max_tokens": params.pop("max_completion_tokens", MAX_OUTPUT_TOKENS)}
    ),
    "temperature": lambda params: params.pop("temperature", None),
}


def _unsupported_param(error: Exception) -> str | None:
    """Достаёт имя параметра, который модель не приняла."""
    text = str(error)
    if "unsupported" not in text.lower():
        return None
    for name in _PARAM_FIXES:
        if f"'{name}'" in text or f'"{name}"' in text:
            return name
    return None


def _create_completion(params: dict, max_fixes: int = 3):
    """Вызывает OpenAI, на ходу подстраивая параметры под конкретную модель."""
    for _ in range(max_fixes + 1):
        try:
            return _client().chat.completions.create(**params)
        except Exception as exc:  # noqa: BLE001
            name = _unsupported_param(exc)
            if not name or name not in params:
                raise
            logger.info("Модель не приняла параметр %s — подстраиваю запрос", name)
            _PARAM_FIXES[name](params)
    return _client().chat.completions.create(**params)


# Ошибки, при которых повторять запрос бессмысленно: нужно править .env.
_FATAL_HINTS = (
    ("model_not_found", "модель «{model}» недоступна для вашего ключа — поменяйте OPENAI_MODEL в .env"),
    ("does not exist", "модель «{model}» недоступна для вашего ключа — поменяйте OPENAI_MODEL в .env"),
    ("invalid_api_key", "неверный OPENAI_API_KEY — проверьте ключ в .env"),
    ("incorrect api key", "неверный OPENAI_API_KEY — проверьте ключ в .env"),
    ("insufficient_quota", "на аккаунте OpenAI закончился баланс"),
)


def _fatal_reason(error: Exception, model: str) -> str | None:
    text = str(error).lower()
    for marker, hint in _FATAL_HINTS:
        if marker in text:
            return hint.format(model=model)
    return None


# Описание короче этого числа слов не считаем надёжным признаком английского:
# «no words. just», «just vibes», «hi» — это заглушки, а не язык анкеты.
WEAK_ABOUT_WORDS = 4


def choose_language(
    *, about: str, name: str = "", city: str = "", forced: str = "", default: str = "ru"
) -> str:
    """Язык ответа: принудительный из настроек, иначе язык анкеты, иначе default."""
    if forced and forced != "auto":
        return forced

    about = (about or "").strip()
    language = detect_language(about)
    words = len(re.findall(r"[^\W\d_]+", about))
    if language == "en" and words < WEAK_ABOUT_WORDS:
        language = detect_language(f"{name} {city}")
    return language or detect_language(f"{name} {city}") or default


def _image_part(path: str) -> dict | None:
    file_path = Path(path)
    if not file_path.exists() or file_path.stat().st_size > 8 * 1024 * 1024:
        return None
    mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "low"}}


def postprocess(text: str, max_chars: int) -> str:
    """Убирает кавычки/служебные обёртки и аккуратно подрезает длину."""
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    if len(text) >= 2 and text[0] in "«\"'“" and text[-1] in "»\"'”":
        text = text[1:-1].strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("\n"))
    return (cut[: boundary + 1] if boundary > max_chars * 0.5 else cut.rsplit(" ", 1)[0]).strip()


def generate_reply(
    *,
    name: str = "",
    age=None,
    city: str = "",
    about: str = "",
    tone: str = "romantic",
    max_chars: int = 320,
    extra_instructions: str = "",
    photos: list[str] | None = None,
    use_vision: bool = False,
    model: str | None = None,
    temperature: float | None = None,
    forced_language: str = "",
    attempts: int = 3,
) -> dict:
    """Генерирует первое сообщение для анкеты. Кидает AIError при неудаче."""
    language = choose_language(about=about, name=name, city=city, forced=forced_language)
    strategy = "with_description" if (about or "").strip() else "no_description"
    model = model or settings.OPENAI_MODEL
    temperature = settings.OPENAI_TEMPERATURE if temperature is None else temperature

    system_prompt = build_system_prompt(
        tone=tone, max_chars=max_chars, language=language, extra=extra_instructions
    )
    user_prompt = build_user_prompt(name=name, age=age, city=city, about=about)

    content: list | str = user_prompt
    if use_vision and photos:
        parts = [{"type": "text", "text": user_prompt}]
        for path in photos[:MAX_VISION_PHOTOS]:
            part = _image_part(path)
            if part:
                parts.append(part)
        if len(parts) > 1:
            parts[0]["text"] += (
                "\n\nНиже фотографии из анкеты — используй их только как намёк на увлечения "
                "и атмосферу, не описывай внешность."
            )
            content = parts

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = _create_completion(params)
            raw = (response.choices[0].message.content or "").strip()
            text = postprocess(raw, max_chars)
            if not text:
                raise AIError("модель вернула пустой ответ")
            usage = getattr(response, "usage", None)
            return {
                "text": text,
                "language": language,
                "strategy": strategy,
                "model": model,
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            }
        except Exception as exc:  # noqa: BLE001 — логируем и пробуем ещё раз
            fatal = _fatal_reason(exc, model)
            if fatal:
                raise AIError(fatal) from exc
            last_error = exc
            logger.warning("OpenAI попытка %s/%s не удалась: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2 * attempt)

    raise AIError(f"OpenAI не ответил: {last_error}")
