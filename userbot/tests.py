"""Тесты сценария скрипта на поддельном Дайвинчике.

Подделка повторяет поведение живого бота: клавиатура ['❤️', '💌 📹 🎤', '👎', '💤'],
запрос текста после 💌 и отдельное подтверждение «Отправить это сообщение пользователю?».
"""
import asyncio
from contextlib import asynccontextmanager
from itertools import count
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import sync_to_async
from django.test import TransactionTestCase

from matcher.models import ActionLog, BotConfig, GeneratedMessage, Profile
from userbot import runner
from userbot.runner import DavinchiUserbot, NotAuthorizedError

FAKE_REPLY = {
    "text": "В Ташкенте столько мест, где время идёт медленнее. Какое твоё любимое?",
    "language": "ru",
    "strategy": "no_description",
    "model": "test-model",
    "prompt_tokens": 5,
    "completion_tokens": 7,
}

PROFILE_KEYBOARD = ["❤️", "💌 📹 🎤", "👎", "💤"]
CONFIRM_KEYBOARD = ["💌", "✖️"]
LANGUAGE_BUTTONS = ["🇺🇿 O'zbek tiliga o'tish", "❌ Не интересно"]
PREMIUM_KEYBOARD = ["⭐️Активировать", "Пока без Premium"]
PREMIUM_PRICES_KEYBOARD = ["5 дней • ⭐ 250", "30 дней • ⭐ 500", "90 дней • ⭐ 1000", "← Назад"]

LIKE_WITH_MESSAGE = "💌 📹 🎤"
DISLIKE = "👎"
ASK_TEXT = "Отправь текст, видео или голосовое(до 15сек)."
CONFIRM_QUESTION = "Отправить это сообщение пользователю?"
LIKE_SENT = "Лайк отправлен, ждем ответа."
LANGUAGE_QUESTION = "Botimizda 🇺🇿 o'zbek tili bor. Unga o'tasanmi?"


class ReplyKeyboardMarkup:
    def __init__(self, rows):
        self.rows = rows


class ReplyInlineMarkup:
    """Имя класса важно: по нему runner отличает inline-клавиатуру от обычной."""

    def __init__(self, rows):
        self.rows = rows


def _markup(buttons, inline=False):
    if not buttons:
        return None
    rows = [SimpleNamespace(buttons=[SimpleNamespace(text=text) for text in buttons])]
    return ReplyInlineMarkup(rows) if inline else ReplyKeyboardMarkup(rows)


class FakeMessage:
    def __init__(
        self, message="", *, photo=None, keyboard=None, inline=None, chat_id=777, msg_id=1
    ):
        self.message = message
        self.photo = photo
        self.chat_id = chat_id
        self.id = msg_id
        self.grouped_id = None
        self.reply_markup = _markup(inline, inline=True) if inline else _markup(keyboard)
        self.clicked = None

    async def click(self, filter=None, **kwargs):  # noqa: A002 — имя параметра из Telethon
        for row in self.reply_markup.rows:
            for button in row.buttons:
                if filter is None or filter(button):
                    self.clicked = button.text
                    return
        raise ValueError("подходящая кнопка не найдена")


class FakeClient:
    """Эмулирует Дайвинчик: отвечает на нажатия так же, как живой бот."""

    def __init__(self, *, react: bool = True, confirm_step: bool = True):
        self.sent: list[tuple] = []
        self.react = react
        self.confirm_step = confirm_step
        self.userbot: DavinchiUserbot | None = None
        self._awaiting_text = False
        self._awaiting_confirm = False

    async def send_message(self, chat, text):
        self.sent.append((chat, text))
        if self.react and self.userbot is not None and chat == self.userbot.target:
            await self._react_like_davinchi(text)

    async def _react_like_davinchi(self, text: str) -> None:
        if self._awaiting_confirm:
            if "💌" in text:
                self._awaiting_confirm = False
                await self._deliver(LIKE_SENT, PROFILE_KEYBOARD)
            return
        if self._awaiting_text:
            self._awaiting_text = False
            if self.confirm_step:
                self._awaiting_confirm = True
                await self._deliver(CONFIRM_QUESTION, CONFIRM_KEYBOARD)
            else:
                await self._deliver(LIKE_SENT, PROFILE_KEYBOARD)
            return
        if "💌" in text:
            self._awaiting_text = True
            await self._deliver(ASK_TEXT, PROFILE_KEYBOARD)

    async def _deliver(self, text: str, keyboard: list[str]) -> None:
        await self.userbot.handle_incoming(FakeMessage(text, keyboard=keyboard))

    @asynccontextmanager
    async def action(self, chat, kind):
        yield

    @property
    def texts(self) -> list[str]:
        """Что ушло самому Дайвинчику."""
        target = self.userbot.target if self.userbot else None
        return [text for chat, text in self.sent if chat == target]

    @property
    def notifications(self) -> list[str]:
        """Что ушло вам в «Избранное»."""
        target = self.userbot.target if self.userbot else None
        return [text for chat, text in self.sent if chat != target]


def make_bot(**kwargs) -> tuple[DavinchiUserbot, FakeClient]:
    client = FakeClient(**kwargs)
    bot = DavinchiUserbot(client=client)
    client.userbot = bot
    return bot, client


_card_ids = count(1000)


def profile_message(caption: str, msg_id: int | None = None) -> FakeMessage:
    """Карточка анкеты: у каждой свой id, как у настоящих сообщений Telegram."""
    return FakeMessage(
        caption,
        photo=True,
        keyboard=PROFILE_KEYBOARD,
        msg_id=msg_id if msg_id is not None else next(_card_ids),
    )


@sync_to_async(thread_sensitive=True)
def setup_config(**kwargs) -> None:
    config = BotConfig.get_solo()
    config.min_delay = 0
    config.max_delay = 0
    for key, value in kwargs.items():
        setattr(config, key, value)
    config.save()


@sync_to_async(thread_sensitive=True)
def profile_statuses() -> list[str]:
    return list(Profile.objects.values_list("status", flat=True))


@sync_to_async(thread_sensitive=True)
def sent_texts() -> list[str]:
    return list(
        GeneratedMessage.objects.filter(status=GeneratedMessage.STATUS_SENT).values_list(
            "text", flat=True
        )
    )


@sync_to_async(thread_sensitive=True)
def failure_reasons() -> list[str]:
    return list(
        GeneratedMessage.objects.filter(status=GeneratedMessage.STATUS_FAILED).values_list(
            "error", flat=True
        )
    )


@sync_to_async(thread_sensitive=True)
def skip_reasons() -> list[str]:
    return list(
        Profile.objects.filter(status=Profile.STATUS_SKIPPED).values_list("skip_reason", flat=True)
    )


@sync_to_async(thread_sensitive=True)
def log_events() -> list[str]:
    return list(ActionLog.objects.values_list("event", flat=True))


@patch.object(runner, "TYPING_MIN_SECONDS", 0.0)
@patch.object(runner, "TYPING_MAX_SECONDS", 0.0)
@patch.object(runner, "KICKSTART_DELAY", 0.0)
class UserbotFlowTests(TransactionTestCase):
    reset_sequences = True

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_full_flow_with_confirmation(self, _mocked):
        """💌 → текст → подтверждение 💌 → «Лайк отправлен»."""
        await setup_config()
        bot, client = make_bot()

        await bot.handle_incoming(profile_message(",, 24, Ташкент – no words. just"))

        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE, FAKE_REPLY["text"], "💌"])
        self.assertEqual(await sent_texts(), [FAKE_REPLY["text"]])
        self.assertEqual(await profile_statuses(), [Profile.STATUS_SENT])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_flow_without_confirmation_step(self, _mocked):
        """Если бот подтверждения не спрашивает, сообщение всё равно считается отправленным."""
        await setup_config()
        bot, client = make_bot(confirm_step=False)

        await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE, FAKE_REPLY["text"]])
        self.assertEqual(await sent_texts(), [FAKE_REPLY["text"]])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_age_filter_presses_dislike(self, mocked):
        await setup_config(min_age=25)
        bot, client = make_bot()

        await bot.handle_incoming(profile_message("Аня, 19, Ташкент"))

        self.assertEqual(client.texts, [DISLIKE])
        self.assertFalse(mocked.called)
        self.assertEqual(await profile_statuses(), [Profile.STATUS_SKIPPED])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_duplicate_profile_is_skipped(self, _mocked):
        await setup_config()
        bot, client = make_bot()
        caption = "Аня, 24, Ташкент — кофе и книги"

        await bot.handle_incoming(profile_message(caption))
        await bot.handle_incoming(profile_message(caption))

        self.assertEqual(client.texts.count(DISLIKE), 1)
        self.assertIn("duplicate", await log_events())

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_dry_run_does_not_press_anything(self, _mocked):
        await setup_config()
        client = FakeClient()
        bot = DavinchiUserbot(dry_run=True, client=client)
        client.userbot = bot

        await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        self.assertNotIn(LIKE_WITH_MESSAGE, client.texts)
        self.assertEqual(await sent_texts(), [])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_like_without_prompt_is_not_written_to(self, mocked):
        """Если бот принял лайк без запроса текста — ничего не генерируем и не шлём."""
        await setup_config()
        bot, client = make_bot(react=False)

        task = asyncio.ensure_future(bot.handle_incoming(profile_message("Аня, 24, Ташкент")))
        await asyncio.sleep(0.05)
        await bot.handle_service_message(LIKE_SENT)
        await task

        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE])
        self.assertEqual(await profile_statuses(), [Profile.STATUS_SKIPPED])
        self.assertIn("без текста", (await skip_reasons())[0])
        self.assertFalse(mocked.called)

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_stale_card_is_not_answered(self, mocked):
        """Регрессия: анкету пролистали во время паузы — письмо не должно уйти следующей."""
        await setup_config()
        bot, client = make_bot()
        card = profile_message("Аня, 24, Ташкент")
        original_delay = bot._human_delay

        async def delay_then_next_card(config, factor=1.0):
            await original_delay(config, factor)
            bot._current_card_id = card.id + 1  # на экране уже следующая анкета

        bot._human_delay = delay_then_next_card

        await bot.handle_profile(card.message, [card])

        self.assertEqual(client.texts, [])
        self.assertFalse(mocked.called)
        self.assertEqual(await profile_statuses(), [Profile.STATUS_SKIPPED])
        self.assertIn("другая анкета", (await skip_reasons())[0])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_card_swapped_while_bot_asked_for_text(self, _mocked):
        """Анкету пролистали после лайка: старый текст не уходит, пишем той, что на экране."""
        await setup_config()

        class SwappingClient(FakeClient):
            """Эмулирует ручное пролистывание анкеты сразу после нашего лайка."""

            swap = True

            async def _deliver(self, text, keyboard):
                await super()._deliver(text, keyboard)
                if text == ASK_TEXT and self.swap:
                    self.swap = False
                    self.userbot._current_card_id += 1

        client = SwappingClient()
        bot = DavinchiUserbot(client=client)
        client.userbot = bot

        await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        # Лайк нажали, но письмо не отправили — анкета уже не та.
        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE])
        self.assertEqual(await sent_texts(), [])
        self.assertIn("пролистали", (await skip_reasons())[0])

        # Следующая анкета подхватывает открытый запрос текста без повторного лайка.
        await bot.handle_incoming(profile_message("Лена, 23, Ташкент — люблю горы"))

        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE, FAKE_REPLY["text"], "💌"])
        self.assertEqual(await sent_texts(), [FAKE_REPLY["text"]])
        self.assertIn("prompt_reused", await log_events())

    async def test_text_is_generated_only_after_the_like(self):
        """Порядок важен: сначала лайк, потом генерация — иначе текст уходит не туда."""
        await setup_config()
        bot, client = make_bot()
        seen_before_generation = []

        def spy(**kwargs):
            seen_before_generation.append(list(client.texts))
            return dict(FAKE_REPLY)

        with patch("matcher.ai.generate_reply", side_effect=spy):
            await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        self.assertEqual(seen_before_generation, [[LIKE_WITH_MESSAGE]])
        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE, FAKE_REPLY["text"], "💌"])

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_daily_limit_blocks_processing(self, mocked):
        await setup_config(daily_limit=0)
        bot, client = make_bot()

        await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        self.assertEqual(client.texts, [])
        self.assertFalse(mocked.called)

    async def test_no_more_profiles_pauses_bot(self):
        await setup_config()
        bot, _client = make_bot()

        await bot.handle_service_message("Анкеты закончились, попробуй позже")

        config = await sync_to_async(BotConfig.get_solo, thread_sensitive=True)()
        self.assertTrue(config.is_paused)

    async def test_language_prompt_only_notifies(self):
        """Врезки бот не трогает: сообщает вам и ждёт, пока вы закроете сами."""
        await setup_config()
        bot, client = make_bot()
        message = FakeMessage(LANGUAGE_QUESTION, inline=LANGUAGE_BUTTONS)

        await bot.handle_incoming(message)

        self.assertIsNone(message.clicked)
        self.assertEqual(client.texts, [])
        self.assertEqual(len(client.notifications), 1)
        self.assertIn("Закрой его сам", client.notifications[0])
        self.assertIn("needs_attention", await log_events())

    async def test_premium_ad_only_notifies(self):
        await setup_config()
        bot, client = make_bot()

        await bot.handle_incoming(FakeMessage("Активируй Premium", keyboard=PREMIUM_KEYBOARD))

        self.assertEqual(client.texts, [])
        self.assertEqual(len(client.notifications), 1)

    async def test_repeated_prompts_do_not_spam_notifications(self):
        await setup_config()
        bot, client = make_bot()

        for _ in range(5):
            await bot.handle_incoming(FakeMessage("Активируй Premium", keyboard=PREMIUM_KEYBOARD))

        self.assertEqual(len(client.notifications), 1)

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    async def test_work_resumes_after_user_closes_prompt(self, _mocked):
        """Как только анкеты снова пошли, скрипт продолжает сам — без перезапуска."""
        await setup_config()
        bot, client = make_bot()

        await bot.handle_incoming(FakeMessage("Активируй Premium", keyboard=PREMIUM_KEYBOARD))
        self.assertEqual(client.texts, [])

        await bot.handle_incoming(profile_message("Аня, 24, Ташкент"))

        self.assertEqual(client.texts, [LIKE_WITH_MESSAGE, FAKE_REPLY["text"], "💌"])
        self.assertEqual(await sent_texts(), [FAKE_REPLY["text"]])

    async def test_kickstart_is_silent_when_profiles_are_shown(self):
        """Клавиатура анкеты — это норма: ничего не жмём и не дёргаем пользователя."""
        await setup_config()
        bot, client = make_bot()
        bot.remember_keyboard(FakeMessage("✨🔍", keyboard=PROFILE_KEYBOARD))

        await bot._maybe_kickstart(force=True)

        self.assertEqual(client.texts, [])
        self.assertEqual(client.notifications, [])

    async def test_kickstart_never_presses_paid_buttons(self):
        """Главное: в меню покупки Premium нельзя нажать «90 дней • ⭐ 1000»."""
        await setup_config()
        bot, client = make_bot()
        bot.remember_keyboard(FakeMessage("Выбери срок", keyboard=PREMIUM_PRICES_KEYBOARD))

        await bot._maybe_kickstart(force=True)

        self.assertEqual(client.texts, [])
        self.assertIn("waiting_for_user", await log_events())

    async def test_kickstart_waits_when_screen_is_unknown(self):
        """Если выйти некуда — просто ждём, наугад ничего не жмём."""
        await setup_config()
        bot, client = make_bot()
        bot.remember_keyboard(FakeMessage("Выбери срок", keyboard=PREMIUM_PRICES_KEYBOARD[:3]))

        await bot._maybe_kickstart(force=True)

        self.assertEqual(client.texts, [])
        self.assertIn("waiting_for_user", await log_events())

    async def test_kickstart_uses_menu_button_from_keyboard(self):
        await setup_config()
        bot, client = make_bot()
        bot.remember_keyboard(FakeMessage("Меню", keyboard=["1 🚀", "2 ✏️", "3 🖼", "4 📝"]))

        await bot._maybe_kickstart(force=True)

        self.assertEqual(client.texts, ["1 🚀"])

    async def test_unknown_bot_message_goes_to_log(self):
        await setup_config()
        bot, _client = make_bot()

        await bot.handle_service_message("Какая-то новая фраза от бота")

        self.assertIn("bot_message", await log_events())


class ClientLifecycleTests(TransactionTestCase):
    """Telethon сам не создаёт цикл событий, поэтому клиент рождается только внутри run()."""

    def test_constructor_works_without_event_loop(self):
        bot = DavinchiUserbot()
        self.assertIsNone(bot.client)

    def test_run_requires_authorized_session(self):
        class UnauthorizedClient:
            def __init__(self):
                self.disconnected_called = False

            async def connect(self):
                return True

            async def is_user_authorized(self):
                return False

            async def disconnect(self):
                self.disconnected_called = True

        client = UnauthorizedClient()
        bot = DavinchiUserbot(client=client)
        with self.assertRaises(NotAuthorizedError) as ctx:
            asyncio.run(bot.run())
        self.assertIn("login_telegram", str(ctx.exception))
        self.assertTrue(client.disconnected_called)

    def test_run_starts_and_stops_cleanly(self):
        """Регрессия: client.disconnected — Future, его нельзя передавать в create_task."""

        class ConnectedClient(FakeClient):
            def __init__(self):
                super().__init__(react=False)
                self.disconnect_called = False
                self.handlers: list = []
                self._disconnected_future = None

            async def connect(self):
                self._disconnected_future = asyncio.get_running_loop().create_future()
                return True

            async def is_user_authorized(self):
                return True

            async def get_me(self):
                return SimpleNamespace(first_name="Отабек", id=1)

            @property
            def disconnected(self):
                return asyncio.shield(self._disconnected_future)

            def on(self, event):
                def decorator(func):
                    self.handlers.append((event, func))
                    return func

                return decorator

            async def disconnect(self):
                self.disconnect_called = True
                if self._disconnected_future and not self._disconnected_future.done():
                    self._disconnected_future.set_result(True)

        client = ConnectedClient()
        bot = DavinchiUserbot(client=client)

        async def scenario():
            task = asyncio.ensure_future(bot.run(kickstart=False))
            await asyncio.sleep(0.05)
            bot._stop_event.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())

        self.assertTrue(client.disconnect_called)
        self.assertEqual(len(client.handlers), 2)  # альбомы и одиночные сообщения
