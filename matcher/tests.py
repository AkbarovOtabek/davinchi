"""Тесты: разбор анкет, лимиты, API."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from matcher import ai, services
from matcher.ai import choose_language, postprocess
from matcher.models import BotConfig, GeneratedMessage, Profile
from matcher.parser import classify_bot_message, detect_language, parse_profile_caption

FAKE_REPLY = {
    "text": "Ташкент в 24 — это же лучший возраст для спонтанных прогулок. Куда бы ты сбежала на выходные?",
    "language": "ru",
    "strategy": "no_description",
    "model": "test-model",
    "prompt_tokens": 10,
    "completion_tokens": 20,
}


class ParserTests(TestCase):
    def test_full_caption(self):
        parsed = parse_profile_caption("Аня, 25, Ташкент — люблю горы и кофе")
        self.assertEqual(parsed["name"], "Аня")
        self.assertEqual(parsed["age"], 25)
        self.assertEqual(parsed["city"], "Ташкент")
        self.assertEqual(parsed["about"], "люблю горы и кофе")
        self.assertEqual(parsed["language"], "ru")

    def test_caption_without_description(self):
        parsed = parse_profile_caption("Марина, 22, Ташкент")
        self.assertEqual(parsed["about"], "")
        self.assertEqual(parsed["age"], 22)

    def test_caption_without_name(self):
        parsed = parse_profile_caption(",, 24, Ташкент – no words. just")
        self.assertEqual(parsed["name"], "")
        self.assertEqual(parsed["city"], "Ташкент")
        self.assertEqual(parsed["about"], "no words. just")

    def test_city_with_hyphen_is_not_split(self):
        parsed = parse_profile_caption("Ира, 27, Санкт-Петербург — читаю фантастику")
        self.assertEqual(parsed["city"], "Санкт-Петербург")
        self.assertEqual(parsed["about"], "читаю фантастику")

    def test_multiline_description(self):
        parsed = parse_profile_caption("Лена, 23, Ташкент\nищу друга\nи хорошие книги")
        self.assertEqual(parsed["about"], "ищу друга\nи хорошие книги")

    def test_not_a_profile(self):
        self.assertIsNone(parse_profile_caption("Отправь текст, видео или голосовое(до 15сек)."))

    def test_language_detection(self):
        self.assertEqual(detect_language("люблю кофе"), "ru")
        self.assertEqual(detect_language("kitob o'qishni yaxshi ko'raman"), "uz")
        self.assertEqual(detect_language("I love hiking and books"), "en")
        self.assertEqual(detect_language("   "), "")

    def test_service_messages(self):
        self.assertEqual(
            classify_bot_message("Отправь текст, видео или голосовое(до 15сек)."), "ask_message"
        )
        self.assertEqual(classify_bot_message("Лайк отправлен, ждем ответа."), "like_sent")
        self.assertEqual(classify_bot_message("1. Смотреть анкеты."), "menu")


class PostprocessTests(TestCase):
    def test_strips_quotes(self):
        self.assertEqual(postprocess('«Привет, как настроение?»', 300), "Привет, как настроение?")

    def test_trims_to_sentence(self):
        text = "Первое предложение. Второе предложение, которое уже не влезает в лимит."
        self.assertTrue(postprocess(text, 30).endswith("."))
        self.assertLessEqual(len(postprocess(text, 30)), 30)


class ServiceTests(TestCase):
    def setUp(self):
        self.config = BotConfig.get_solo()

    def test_daily_limit_blocks(self):
        self.config.daily_limit = 1
        self.config.save()
        profile = services.save_profile(
            {"name": "Аня", "age": 22, "city": "Ташкент", "about": ""}, chat_id=1
        )
        GeneratedMessage.objects.create(
            profile=profile,
            text="раз",
            status=GeneratedMessage.STATUS_SENT,
            sent_at=timezone.now(),
        )
        allowed, reason = services.can_run(self.config)
        self.assertFalse(allowed)
        self.assertIn("лимит", reason)

    def test_age_filter(self):
        self.config.min_age, self.config.max_age = 20, 30
        self.config.save()
        ok, reason = services.passes_filters({"age": 18, "city": "Ташкент"}, self.config)
        self.assertFalse(ok)
        self.assertIn("18", reason)

    def test_city_filter(self):
        self.config.city_filter = "ташкент"
        self.config.save()
        ok, _ = services.passes_filters({"age": 22, "city": "Самарканд"}, self.config)
        self.assertFalse(ok)

    def test_duplicate_detection(self):
        data = {"name": "Аня", "age": 22, "city": "Ташкент", "about": "кофе"}
        profile = services.save_profile(data, chat_id=1)
        found = services.find_duplicate(profile.fingerprint)
        self.assertEqual(found.pk, profile.pk)

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    def test_generate_for_profile(self, mocked):
        profile = services.save_profile(
            {"name": "Аня", "age": 24, "city": "Ташкент", "about": ""}, chat_id=1
        )
        message = services.generate_for_profile(profile, self.config)
        profile.refresh_from_db()
        self.assertEqual(profile.status, Profile.STATUS_GENERATED)
        self.assertEqual(message.strategy, "no_description")
        self.assertTrue(mocked.called)

        services.mark_sent(message)
        profile.refresh_from_db()
        self.assertEqual(profile.status, Profile.STATUS_SENT)
        self.assertEqual(services.sent_today(), 1)


class ApiTests(TestCase):
    def test_parse_endpoint(self):
        response = self.client.post(
            reverse("parse"),
            {"caption": "Аня, 25, Ташкент — люблю горы"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["city"], "Ташкент")

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    def test_generate_endpoint(self, mocked):
        response = self.client.post(
            reverse("generate"),
            {"raw_caption": ",, 24, Ташкент"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], FAKE_REPLY["text"])
        self.assertEqual(mocked.call_args.kwargs["age"], 24)

    @patch("matcher.ai.generate_reply", return_value=dict(FAKE_REPLY))
    def test_generate_and_save(self, _mocked):
        response = self.client.post(
            reverse("generate"),
            {"raw_caption": "Аня, 24, Ташкент — кофе и книги", "save": True},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Profile.objects.count(), 1)
        self.assertEqual(GeneratedMessage.objects.count(), 1)

    def test_config_patch(self):
        response = self.client.patch(
            reverse("config"), {"tone": "playful", "daily_limit": 5}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        config = BotConfig.get_solo()
        self.assertEqual(config.tone, "playful")
        self.assertEqual(config.daily_limit, 5)

    def test_pause_and_health(self):
        response = self.client.post(
            reverse("pause"), {"minutes": 30}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        health = self.client.get(reverse("health")).json()
        self.assertFalse(health["can_run"])
        self.assertIn("пауза", health["reason"])

    def test_stats(self):
        response = self.client.get(reverse("stats"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("sent_today", response.json())


class OpenAIParamAdaptationTests(TestCase):
    """Разные модели принимают разные параметры — запрос должен подстраиваться."""

    @staticmethod
    def _fake_client(reject: str, hint: str):
        calls: list[dict] = []

        class Completions:
            def create(self, **params):
                calls.append(dict(params))
                if reject in params:
                    raise RuntimeError(
                        f"Error code: 400 - Unsupported parameter: '{reject}' is not supported "
                        f"with this model. {hint}"
                    )
                return "ok"

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        return client, calls

    def test_switches_to_max_completion_tokens(self):
        client, calls = self._fake_client("max_tokens", "Use 'max_completion_tokens' instead.")
        with patch("matcher.ai._client", return_value=client):
            result = ai._create_completion({"model": "m", "messages": [], "max_tokens": 400})
        self.assertEqual(result, "ok")
        self.assertIn("max_completion_tokens", calls[-1])
        self.assertNotIn("max_tokens", calls[-1])

    def test_drops_unsupported_temperature(self):
        client, calls = self._fake_client("temperature", "Only the default value is supported.")
        with patch("matcher.ai._client", return_value=client):
            result = ai._create_completion({"model": "m", "messages": [], "temperature": 0.9})
        self.assertEqual(result, "ok")
        self.assertNotIn("temperature", calls[-1])

    def test_other_errors_are_not_swallowed(self):
        class Completions:
            def create(self, **params):
                raise RuntimeError("Error code: 401 - Incorrect API key provided")

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        with patch("matcher.ai._client", return_value=client):
            with self.assertRaises(RuntimeError):
                ai._create_completion({"model": "m", "messages": []})


class FatalErrorTests(TestCase):
    """На неверном ключе или недоступной модели повторять запрос бессмысленно."""

    def _raise_from_openai(self, message: str):
        class Completions:
            def create(self, **params):
                raise RuntimeError(message)

        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    def test_unknown_model_reported_clearly(self):
        client = self._raise_from_openai(
            "Error code: 404 - {'error': {'code': 'model_not_found'}}"
        )
        with patch("matcher.ai._client", return_value=client), patch("matcher.ai.time.sleep") as slept:
            with self.assertRaises(ai.AIError) as ctx:
                ai.generate_reply(name="Аня", age=24, city="Ташкент", model="gpt-6-luna")
        self.assertIn("gpt-6-luna", str(ctx.exception))
        self.assertIn("OPENAI_MODEL", str(ctx.exception))
        self.assertFalse(slept.called)  # без бессмысленных ретраев

    def test_invalid_key_reported_clearly(self):
        client = self._raise_from_openai("Error code: 401 - Incorrect API key provided")
        with patch("matcher.ai._client", return_value=client), patch("matcher.ai.time.sleep"):
            with self.assertRaises(ai.AIError) as ctx:
                ai.generate_reply(name="Аня", age=24, city="Ташкент")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))


class ChooseLanguageTests(TestCase):
    def test_description_language_wins(self):
        self.assertEqual(choose_language(about="люблю горы и кофе", city="Ташкент"), "ru")
        self.assertEqual(
            choose_language(about="kitob o'qishni yaxshi ko'raman", city="Toshkent"), "uz"
        )

    def test_short_english_stub_is_ignored(self):
        """«no words. just» — это заглушка, а не английская анкета."""
        self.assertEqual(choose_language(about="no words. just", city="Ташкент"), "ru")
        self.assertEqual(choose_language(about="just", name="Аня", city="Ташкент"), "ru")

    def test_real_english_description_kept(self):
        self.assertEqual(
            choose_language(about="I love hiking, books and long walks", city="Ташкент"), "en"
        )

    def test_no_description_falls_back_to_city(self):
        self.assertEqual(choose_language(about="", name="Марина", city="Ташкент"), "ru")
        self.assertEqual(choose_language(about="", name="", city=""), "ru")

    def test_forced_language_overrides_everything(self):
        self.assertEqual(
            choose_language(about="I love hiking and books", city="Tashkent", forced="ru"), "ru"
        )
        self.assertEqual(choose_language(about="люблю горы", forced="auto"), "ru")
