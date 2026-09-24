"""Скрипт на Telethon: читает анкеты Дайвинчика, генерирует текст и отправляет его.

Сценарий одной анкеты (снят с живого бота, сентябрь 2026):
    1. @leomatchbot присылает фото (или альбом) с подписью «Имя, 24, Ташкент – описание»
       и отдельное сообщение с клавиатурой ['❤️', '💌 📹 🎤', '👎', '💤'];
    2. подпись разбирается, анкета сохраняется в БД;
    3. OpenAI генерирует первое сообщение (есть описание — пишем по описанию, нет —
       «красивое письмо» по возрасту и городу), язык ответа совпадает с языком анкеты;
    4. скрипт жмёт кнопку «лайк с сообщением» (💌) и ждёт «Отправь текст...»;
    5. отправляет сгенерированный текст с имитацией набора;
    6. бот переспрашивает «Отправить это сообщение пользователю?» — жмём 💌;
    7. бот отвечает «Лайк отправлен, ждем ответа» и показывает следующую анкету.

Подписи кнопок не захардкожены: они берутся из клавиатуры последнего сообщения бота,
поэтому смена эмодзи в Дайвинчике ничего не ломает.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import timedelta
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from matcher import ai, services
from matcher.models import ActionLog, BotConfig, GeneratedMessage, Profile
from matcher.parser import classify_bot_message, is_paid_button, parse_profile_caption

logger = logging.getLogger("userbot")


class NotAuthorizedError(RuntimeError):
    """Файла сессии нет или он больше не действителен."""


# Маркеры кнопок: ищем кнопку, в тексте которой встречается маркер.
MESSAGE_LIKE_MARKER = "💌"  # «лайк с сообщением» — сейчас это кнопка «💌 📹 🎤»
LIKE_MARKER = "❤️"
DISLIKE_MARKER = "👎"
SLEEP_MARKER = "💤"
# Кнопка «1. Смотреть анкеты» — это «1 🚀»: цифра 1 и, возможно, эмодзи. Ничего больше:
# иначе в меню покупки Premium под шаблон попала бы кнопка «90 дней • ⭐ 1000».
START_VIEWING_RE = re.compile(r"^\s*1[\s\W]*$")

PROMPT_TIMEOUT = 30  # ждём «Отправь текст...» после нажатия 💌
CONFIRM_TIMEOUT = 30  # ждём «Отправить это сообщение пользователю?» и финальное подтверждение
KICKSTART_COOLDOWN = 120  # не открываем просмотр анкет чаще, чем раз в N секунд
KICKSTART_DELAY = 2.0  # пауза перед нажатием «Смотреть анкеты»
ATTENTION_COOLDOWN = 600  # как часто напоминать, что скрипт ждёт вашего вмешательства
NO_MORE_PAUSE_MINUTES = 60
MAX_PHOTOS = 2

TYPING_CHARS_PER_SECOND = 14.0
TYPING_MIN_SECONDS = 1.5
TYPING_MAX_SECONDS = 12.0

# ------------------------------------------------------------ ORM в асинхроне --
a_get_config = sync_to_async(BotConfig.get_solo, thread_sensitive=True)
a_can_run = sync_to_async(services.can_run, thread_sensitive=True)
a_passes_filters = sync_to_async(services.passes_filters, thread_sensitive=True)
a_find_duplicate = sync_to_async(services.find_duplicate, thread_sensitive=True)
a_save_profile = sync_to_async(services.save_profile, thread_sensitive=True)
a_generate = sync_to_async(services.generate_for_profile, thread_sensitive=True)
a_mark_sent = sync_to_async(services.mark_sent, thread_sensitive=True)
a_mark_failed = sync_to_async(services.mark_failed, thread_sensitive=True)
a_mark_skipped = sync_to_async(services.mark_skipped, thread_sensitive=True)
a_log = sync_to_async(services.log_event, thread_sensitive=True)


@sync_to_async(thread_sensitive=True)
def _save_photos(profile_id: int, paths: list[str]) -> None:
    Profile.objects.filter(pk=profile_id).update(photos=paths)


@sync_to_async(thread_sensitive=True)
def _pause_bot(minutes: int) -> None:
    config = BotConfig.get_solo()
    config.paused_until = timezone.now() + timedelta(minutes=minutes)
    config.save(update_fields=["paused_until"])


class DavinchiUserbot:
    """Держит соединение с Telegram и обрабатывает анкеты по одной."""

    def __init__(self, *, dry_run: bool = False, limit: int | None = None, client=None):
        self.dry_run = dry_run
        self.limit = limit
        self.processed = 0
        self.target = settings.TG_TARGET_BOT
        # Клиент создаётся только внутри работающего цикла событий (см. run):
        # начиная с Python 3.12 Telethon не может получить цикл событий сам.
        self.client = client
        self._keyboard: list[str] = []
        self._last_kickstart = 0.0
        self._last_incoming_at = 0.0
        self._last_sent = ""
        self._last_attention = 0.0
        self._current_card_id = 0
        self._lock = asyncio.Lock()
        self._prompt_event = asyncio.Event()
        self._confirm_event = asyncio.Event()
        self._like_done_event = asyncio.Event()
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------- утилиты --
    async def _human_delay(self, config: BotConfig, factor: float = 1.0) -> None:
        low, high = sorted((config.min_delay, config.max_delay))
        await asyncio.sleep(random.uniform(low, high) * factor)

    async def _type_and_send(self, text: str) -> None:
        """Имитирует набор текста, чтобы поведение не выглядело машинным."""
        typing_time = min(
            max(len(text) / TYPING_CHARS_PER_SECOND, TYPING_MIN_SECONDS), TYPING_MAX_SECONDS
        )
        async with self.client.action(self.target, "typing"):
            await asyncio.sleep(typing_time)
        await self.client.send_message(self.target, text)

    async def _notify(self, text: str) -> None:
        if not settings.TG_NOTIFY_CHAT:
            return
        try:
            await self.client.send_message(settings.TG_NOTIFY_CHAT, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось отправить уведомление: %s", exc)

    def remember_keyboard(self, message) -> None:
        """Запоминает подписи кнопок обычной (не inline) клавиатуры бота."""
        markup = getattr(message, "reply_markup", None)
        if markup is None or type(markup).__name__ == "ReplyInlineMarkup":
            return
        buttons = [
            getattr(button, "text", "")
            for row in getattr(markup, "rows", []) or []
            for button in getattr(row, "buttons", []) or []
        ]
        buttons = [text for text in buttons if text]
        if buttons and buttons != self._keyboard:
            self._keyboard = buttons
            logger.info("Клавиатура бота: %s", buttons)

    def button(self, marker: str, default: str = "") -> str:
        """Подпись кнопки, содержащей маркер (эмодзи меняются — маркер остаётся)."""
        for text in self._keyboard:
            if marker in text:
                return text
        return default or marker

    async def _send(self, text: str) -> None:
        """Единственное место, откуда уходят сообщения боту — тут же запоминаем последнее."""
        self._last_sent = text
        await self.client.send_message(self.target, text)

    async def _press(self, marker: str, default: str = "") -> None:
        await self._send(self.button(marker, default))

    async def _wait_any(self, events: dict[str, asyncio.Event], timeout: float) -> str | None:
        """Ждёт первое из событий. Возвращает его имя или None по таймауту."""
        tasks = {name: asyncio.ensure_future(event.wait()) for name, event in events.items()}
        done, pending = await asyncio.wait(
            tasks.values(), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for name, task in tasks.items():
            if task in done:
                return name
        return None

    async def _download_photos(self, messages, profile_id: int) -> list[str]:
        paths: list[str] = []
        photo_dir = Path(settings.PHOTO_DIR)
        photo_dir.mkdir(parents=True, exist_ok=True)
        for index, message in enumerate(messages):
            if len(paths) >= MAX_PHOTOS:
                break
            if not getattr(message, "photo", None):
                continue
            try:
                path = await message.download_media(
                    file=str(photo_dir / f"{profile_id}_{index}.jpg")
                )
                if path:
                    paths.append(str(path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось скачать фото: %s", exc)
        return paths

    # ------------------------------------------------- обработка одной анкеты --
    async def handle_profile(self, caption: str, messages: list) -> None:
        first = messages[0] if messages else None
        card_id = getattr(first, "id", 0) or 0
        # Запоминаем ДО блокировки: пока крутится предыдущий цикл, сюда уже может
        # прийти следующая карточка — тогда старая станет неактуальной.
        self._current_card_id = card_id

        async with self._lock:
            config = await a_get_config()
            allowed, reason = await a_can_run(config)
            if not allowed:
                await a_log("idle", f"анкета пропущена: {reason}", ActionLog.LEVEL_WARNING)
                return

            parsed = parse_profile_caption(caption)
            if not parsed:
                await a_log("unparsed", caption[:500], ActionLog.LEVEL_WARNING)
                return

            fingerprint = Profile.make_fingerprint(
                parsed["name"], parsed["age"], parsed["city"], parsed["about"]
            )
            duplicate = await a_find_duplicate(fingerprint)
            if duplicate:
                await a_log(
                    "duplicate",
                    f"анкета уже обрабатывалась (#{duplicate.pk})",
                    ActionLog.LEVEL_WARNING,
                )
                await self._human_delay(config, 0.4)
                if self._is_current(card_id):
                    await self._press(DISLIKE_MARKER)
                return

            profile = await a_save_profile(
                parsed,
                chat_id=getattr(first, "chat_id", 0) or 0,
                message_id=getattr(first, "id", 0) or 0,
                grouped_id=getattr(first, "grouped_id", None),
            )
            logger.info("Анкета #%s: %s", profile.pk, profile)
            self._last_attention = 0.0  # поток анкет пошёл — напоминания можно слать снова

            ok, skip_reason = await a_passes_filters(parsed, config)
            if not ok:
                await a_mark_skipped(profile, skip_reason)
                await self._human_delay(config, 0.4)
                if self._is_current(card_id):
                    await self._press(DISLIKE_MARKER)
                return

            if config.use_vision:
                paths = await self._download_photos(messages, profile.pk)
                if paths:
                    profile.photos = paths
                    await _save_photos(profile.pk, paths)

            if self.dry_run or not config.auto_send:
                try:
                    message = await a_generate(profile, config)
                except ai.AIError as exc:
                    await self._notify(f"⚠️ OpenAI не ответил по анкете «{profile}»: {exc}")
                    return
                logger.info("Сгенерировано (%s): %s", message.strategy, message.text)
                await self._notify(f"📝 {profile}\n\n{message.text}")
                await a_log("dry_run", "автоотправка выключена", ActionLog.LEVEL_WARNING, profile)
                return

            if not await self._like_and_write(config, profile, card_id):
                return

            self.processed += 1
            if self.limit and self.processed >= self.limit:
                await a_log("stop", f"обработано {self.processed} анкет — останавливаюсь")
                self._stop_event.set()

    def _is_current(self, card_id: int) -> bool:
        """На экране всё ещё та анкета, с которой мы начали?"""
        return self._current_card_id == card_id

    async def _like_and_write(
        self, config: BotConfig, profile: Profile, card_id: int
    ) -> bool:
        """💌 → «Отправь текст...» → генерация → текст → подтверждение.

        Порядок принципиален: сначала лайк, и только потом текст. Если генерировать
        заранее, между появлением анкеты и нажатием проходит 10–20 секунд, за которые
        анкету можно пролистать вручную — и письмо уйдёт уже следующей девушке.
        """
        # Если запрос текста уже висит с прошлого круга (анкету пролистали сразу после
        # нашего лайка), бот ждёт текст именно для этой, текущей анкеты — лайк не нужен.
        prompt_already_open = self._prompt_event.is_set()
        self._confirm_event.clear()
        self._like_done_event.clear()

        if not config.auto_like:
            await a_mark_skipped(profile, "auto_like выключен — лайк с сообщением невозможен")
            return False

        if prompt_already_open:
            await a_log("prompt_reused", "бот уже просит текст — пишу без повторного лайка", profile=profile)
            await self._human_delay(config, 0.2)
        else:
            self._prompt_event.clear()
            await self._human_delay(config, 0.3)
            if not self._is_current(card_id):
                await a_mark_skipped(profile, "на экране уже другая анкета — лайк не ставлю")
                return False

            await self._press(MESSAGE_LIKE_MARKER)

            step = await self._wait_any(
                {"prompt": self._prompt_event, "like": self._like_done_event}, PROMPT_TIMEOUT
            )
            if step == "like":
                await a_mark_skipped(profile, "Дайвинчик принял лайк без текста")
                return False
            if step is None:
                await a_mark_skipped(profile, f"не дождался запроса текста за {PROMPT_TIMEOUT} сек")
                return False
            if not self._is_current(card_id):
                # Анкету пролистали, пока бот просил текст. Флаг запроса намеренно
                # оставляем взведённым: его подхватит анкета, которая сейчас на экране.
                await a_mark_skipped(profile, "анкету пролистали, пока бот просил текст")
                return False

        # Бот ждёт текст именно для этой анкеты — теперь можно спокойно генерировать.
        self._prompt_event.clear()
        try:
            message = await a_generate(profile, config)
        except ai.AIError as exc:
            await self._notify(
                f"⚠️ OpenAI не ответил по анкете «{profile}»: {exc}\n"
                "Бот ждёт текст — ответь ему сам или пролистай анкету."
            )
            return False

        logger.info("Сгенерировано (%s): %s", message.strategy, message.text)
        await self._type_and_send(message.text)

        step = await self._wait_any(
            {"confirm": self._confirm_event, "like": self._like_done_event}, CONFIRM_TIMEOUT
        )
        if step == "confirm":
            await self._human_delay(config, 0.3)
            await self._press(MESSAGE_LIKE_MARKER)
            if await self._wait_any({"like": self._like_done_event}, CONFIRM_TIMEOUT) is None:
                await a_mark_failed(message, "бот не подтвердил отправку после нажатия 💌")
                return False
        elif step is None:
            await a_mark_failed(message, "бот не ответил ни подтверждением, ни «лайк отправлен»")
            return False

        await a_mark_sent(message)
        return True

    # ----------------------------------------------------- служебные события --
    async def handle_incoming(self, message) -> None:
        """Единая точка входа для любого сообщения от бота."""
        self.remember_keyboard(message)
        self._last_incoming_at = time.monotonic()
        text = getattr(message, "message", "") or ""

        if getattr(message, "photo", None) and parse_profile_caption(text):
            await self.handle_profile(text, [message])
            return

        # Реклама Premium, вопрос про язык и прочие врезки блокируют поток анкет.
        # Скрипт их не трогает — ни одна кнопка бота не нажимается наугад. Он просто
        # ждёт, пока вы закроете экран сами, и продолжает работу автоматически.
        if not self._lock.locked() and self._blocks_profiles(message):
            await self._ask_for_help(text)
            return

        await self.handle_service_message(text)

    def _keyboard_of(self, message) -> list[str]:
        markup = getattr(message, "reply_markup", None)
        if markup is None:
            return []
        return [
            getattr(button, "text", "") or ""
            for row in getattr(markup, "rows", []) or []
            for button in getattr(row, "buttons", []) or []
        ]

    @staticmethod
    def _has_profile_buttons(buttons: list[str]) -> bool:
        """Клавиатура анкеты: ❤️ / 💌 / 👎 / 💤 — значит, бот показывает анкеты."""
        markers = (MESSAGE_LIKE_MARKER, LIKE_MARKER, DISLIKE_MARKER, SLEEP_MARKER)
        return any(marker in button for button in buttons for marker in markers)

    def _blocks_profiles(self, message) -> bool:
        """Экран бота, на котором поток анкет стоит: кнопки есть, но они не наши."""
        buttons = [text for text in self._keyboard_of(message) if text]
        if not buttons or self._has_profile_buttons(buttons):
            return False
        return not any(START_VIEWING_RE.match(button) for button in buttons)

    async def _ask_for_help(self, text: str) -> None:
        """Просит закрыть экран вручную — не чаще раза в ATTENTION_COOLDOWN секунд."""
        if time.monotonic() - self._last_attention < ATTENTION_COOLDOWN:
            return
        self._last_attention = time.monotonic()
        await a_log("needs_attention", text[:300], ActionLog.LEVEL_WARNING)
        logger.warning("Жду: бот показывает экран, который я не трогаю")
        await self._notify(
            "⏸ Дайвинчик показывает экран, который я не трогаю (реклама, вопрос про "
            "язык и т.п.). Закрой его сам — я продолжу автоматически.\n\n"
            f"{text[:300]}"
        )

    def _start_viewing_button(self) -> str | None:
        """Кнопка «Смотреть анкеты» в текущей клавиатуре бота."""
        if not self._keyboard:
            return "1"  # историю чата прочитать не удалось — пробуем стандартный пункт меню
        for text in self._keyboard:
            if START_VIEWING_RE.match(text) and not is_paid_button(text):
                return text
        return None

    async def _maybe_kickstart(self, force: bool = False) -> None:
        """Открывает просмотр анкет, но не чаще раза в KICKSTART_COOLDOWN секунд."""
        if not force and time.monotonic() - self._last_kickstart < KICKSTART_COOLDOWN:
            return
        config = await a_get_config()
        allowed, reason = await a_can_run(config)
        if not allowed:
            logger.info("Просмотр анкет не открываю: %s", reason)
            return

        if self._has_profile_buttons(self._keyboard):
            # Анкеты уже показываются — просто ждём следующую, ничего нажимать не надо.
            logger.info("Бот уже показывает анкеты, жду следующую")
            return

        button = self._start_viewing_button()
        if button is None:
            # Бот стоит на чужом экране (реклама, покупка Premium и т.п.) — ничего
            # не нажимаем, ждём, пока вы закроете его сами.
            await a_log(
                "waiting_for_user",
                f"кнопки «Смотреть анкеты» сейчас нет, клавиатура: {self._keyboard}",
                ActionLog.LEVEL_WARNING,
            )
            await self._ask_for_help(f"Текущие кнопки бота: {self._keyboard}")
            return

        self._last_kickstart = time.monotonic()
        await asyncio.sleep(KICKSTART_DELAY)
        await self._send(button)
        logger.info("Открываю просмотр анкет кнопкой «%s»", button)

    async def _sync_state(self):
        """Читает хвост переписки: какая сейчас клавиатура и не висит ли анкета без ответа.

        Возвращает анкету, на которую вы ещё не ответили, или None.
        """
        if not hasattr(self.client, "iter_messages"):
            return None
        pending = None
        last_outgoing_id = 0
        try:
            async for message in self.client.iter_messages(self.target, limit=15):
                if getattr(message, "out", False):
                    last_outgoing_id = max(last_outgoing_id, getattr(message, "id", 0) or 0)
                    continue
                if not self._keyboard:
                    self.remember_keyboard(message)
                text = getattr(message, "message", "") or ""
                if pending is None and getattr(message, "photo", None):
                    if parse_profile_caption(text):
                        pending = message
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось прочитать состояние чата: %s", exc)
            return None

        if pending is not None and (getattr(pending, "id", 0) or 0) > last_outgoing_id:
            logger.info("На экране висит анкета без ответа — беру её в работу")
            return pending
        return None

    async def handle_service_message(self, text: str) -> None:
        kind = classify_bot_message(text)
        if kind == "ask_message":
            self._prompt_event.set()
        elif kind == "confirm_send":
            self._confirm_event.set()
        elif kind == "like_sent":
            self._like_done_event.set()
        elif kind == "mutual":
            await a_log("mutual", text[:500])
            await self._notify(f"💚 Взаимная симпатия!\n\n{text[:500]}")
        elif kind == "no_more":
            await a_log("no_more", text[:300], ActionLog.LEVEL_WARNING)
            await _pause_bot(NO_MORE_PAUSE_MINUTES)
            await self._notify(f"😴 Анкеты закончились, пауза {NO_MORE_PAUSE_MINUTES} минут.")
        elif kind == "limit":
            await a_log("rate_limit", text[:300], ActionLog.LEVEL_WARNING)
            await _pause_bot(15)
        elif kind == "menu":
            await a_log("menu", "получено меню Дайвинчика")
            await self._maybe_kickstart()
        elif text.strip():
            # Всё незнакомое пишем в журнал — по нему видно, если бот сменил формулировки.
            await a_log("bot_message", text[:300])

    # --------------------------------------------------------------- запуск --
    def _register_handlers(self) -> None:
        from telethon import events

        @self.client.on(events.Album(chats=self.target))
        async def on_album(event):  # noqa: ANN001
            for message in event.messages:
                self.remember_keyboard(message)
            caption = next((m.message for m in event.messages if m.message), "")
            await self.handle_profile(caption, list(event.messages))

        @self.client.on(events.NewMessage(chats=self.target, incoming=True))
        async def on_message(event):  # noqa: ANN001
            if event.message.grouped_id:  # альбомы разбирает on_album
                return
            await self.handle_incoming(event.message)

    def _build_client(self):
        """Создаёт TelegramClient. Вызывать только из корутины — нужен активный цикл событий."""
        from telethon import TelegramClient

        return TelegramClient(
            settings.TG_SESSION,
            settings.TG_API_ID,
            settings.TG_API_HASH,
            device_model="Davinchi Assistant",
            system_version="Windows 11",
        )

    async def run(self, kickstart: bool = True) -> None:
        if self.client is None:
            self.client = self._build_client()

        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise NotAuthorizedError(
                "Аккаунт Telegram не авторизован. Выполните: python manage.py login_telegram"
            )

        me = await self.client.get_me()
        logger.info("Вошёл как %s (id=%s)", me.first_name, me.id)
        self._register_handlers()
        pending = await self._sync_state()
        await a_log("start", f"скрипт запущен, dry_run={self.dry_run}")

        if pending is not None:
            # Анкета уже показана и осталась без ответа — продолжаем с неё.
            await self.handle_profile(getattr(pending, "message", "") or "", [pending])
        elif kickstart:
            await self._maybe_kickstart()

        # client.disconnected — это Future, а не корутина, поэтому ensure_future, не create_task.
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        disconnect_task = asyncio.ensure_future(self.client.disconnected)
        _done, pending = await asyncio.wait(
            {stop_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await a_log("stop", "скрипт остановлен")
        await self.client.disconnect()
