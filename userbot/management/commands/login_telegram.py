"""python manage.py login_telegram — разовая авторизация в Telegram (создаёт .session)."""
import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Авторизует ваш аккаунт Telegram и сохраняет файл сессии."

    def handle(self, *args, **options):
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            raise CommandError("Заполните TG_API_ID и TG_API_HASH в .env (my.telegram.org).")

        from telethon import TelegramClient

        async def main():
            client = TelegramClient(settings.TG_SESSION, settings.TG_API_ID, settings.TG_API_HASH)
            await client.start(phone=settings.TG_PHONE or (lambda: input("Номер телефона: ")))
            me = await client.get_me()
            self.stdout.write(
                self.style.SUCCESS(f"Готово: вошли как {me.first_name} (@{me.username}, id={me.id})")
            )
            self.stdout.write(f"Файл сессии: {settings.TG_SESSION}")
            await client.disconnect()

        asyncio.run(main())
