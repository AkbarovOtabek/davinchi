"""python manage.py run_userbot — основной цикл: читает анкеты и отвечает."""
import asyncio

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Запускает юзербота: читает анкеты Дайвинчика и отправляет сгенерированные сообщения."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Генерировать текст, но не отправлять (результат придёт в «Избранное»).",
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="Остановиться после N отправленных сообщений."
        )
        parser.add_argument(
            "--no-kickstart",
            action="store_true",
            help="Не отправлять «1» при старте (не открывать просмотр анкет автоматически).",
        )

    def handle(self, *args, **options):
        from userbot.runner import DavinchiUserbot, NotAuthorizedError

        bot = DavinchiUserbot(dry_run=options["dry_run"], limit=options["limit"])
        self.stdout.write(self.style.SUCCESS("Запускаю юзербота. Ctrl+C — остановить."))
        try:
            asyncio.run(bot.run(kickstart=not options["no_kickstart"]))
        except NotAuthorizedError as exc:
            raise CommandError(str(exc)) from exc
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Остановлено пользователем."))
