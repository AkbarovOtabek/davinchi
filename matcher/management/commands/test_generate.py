"""python manage.py test_generate — проверка генерации без Telegram."""
from django.core.management.base import BaseCommand

from matcher import ai
from matcher.models import BotConfig
from matcher.parser import parse_profile_caption

SAMPLES = [
    ",, 24, Ташкент – no words. just",
    "Марина, 22, Ташкент",
    "Nilufar, 21, Toshkent - kitob o'qishni va sayohat qilishni yaxshi ko'raman",
    "Аня, 25, Ташкент — люблю горы, кофе и вечерние пробежки",
]


class Command(BaseCommand):
    help = "Генерирует сообщения для тестовых анкет (или для подписи из --caption)."

    def add_arguments(self, parser):
        parser.add_argument("--caption", type=str, default=None, help="Своя подпись анкеты.")

    def handle(self, *args, **options):
        config = BotConfig.get_solo()
        captions = [options["caption"]] if options["caption"] else SAMPLES

        for caption in captions:
            parsed = parse_profile_caption(caption)
            self.stdout.write(self.style.HTTP_INFO(f"\n--- {caption}"))
            if not parsed:
                self.stdout.write(self.style.ERROR("не распознано как анкета"))
                continue
            self.stdout.write(f"разбор: {parsed}")
            try:
                result = ai.generate_reply(
                    name=parsed["name"],
                    age=parsed["age"],
                    city=parsed["city"],
                    about=parsed["about"],
                    tone=config.tone,
                    max_chars=config.max_chars,
                    extra_instructions=config.extra_instructions,
                )
            except ai.AIError as exc:
                self.stdout.write(self.style.ERROR(f"ошибка: {exc}"))
                continue
            self.stdout.write(
                self.style.SUCCESS(f"[{result['strategy']}/{result['language']}] {result['text']}")
            )
