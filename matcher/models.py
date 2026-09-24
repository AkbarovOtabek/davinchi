"""Модели: настройки бота, анкеты из Дайвинчика и сгенерированные сообщения."""
from __future__ import annotations

import hashlib

from django.db import models
from django.utils import timezone


class BotConfig(models.Model):
    """Singleton с настройками, которые можно менять на лету через API/админку."""

    SINGLETON_PK = 1

    TONE_WARM = "warm"
    TONE_ROMANTIC = "romantic"
    TONE_PLAYFUL = "playful"
    TONE_CHOICES = [
        (TONE_WARM, "Тёплый и лёгкий"),
        (TONE_ROMANTIC, "Романтично-красивый"),
        (TONE_PLAYFUL, "Игривый с юмором"),
    ]

    enabled = models.BooleanField("Бот включён", default=True)
    auto_like = models.BooleanField("Ставить лайк автоматически", default=True)
    auto_send = models.BooleanField("Отправлять текст автоматически", default=True)
    use_vision = models.BooleanField("Отдавать фото в модель (vision)", default=False)

    tone = models.CharField("Тон", max_length=16, choices=TONE_CHOICES, default=TONE_ROMANTIC)
    language = models.CharField(
        "Язык ответа",
        max_length=8,
        choices=[
            ("auto", "Как в анкете"),
            ("ru", "Всегда русский"),
            ("uz", "Всегда узбекский"),
            ("en", "Всегда английский"),
        ],
        default="auto",
    )
    max_chars = models.PositiveIntegerField("Максимум символов в сообщении", default=320)
    extra_instructions = models.TextField("Доп. инструкции для модели", blank=True, default="")

    daily_limit = models.PositiveIntegerField("Лимит сообщений в сутки", default=40)
    min_delay = models.FloatField("Мин. пауза перед действием, сек", default=4.0)
    max_delay = models.FloatField("Макс. пауза перед действием, сек", default=14.0)
    paused_until = models.DateTimeField("Пауза до", null=True, blank=True)

    min_age = models.PositiveSmallIntegerField("Мин. возраст анкеты", default=18)
    max_age = models.PositiveSmallIntegerField("Макс. возраст анкеты", default=99)
    city_filter = models.CharField(
        "Фильтр по городу (подстрока, пусто = любой)", max_length=128, blank=True, default=""
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки бота"
        verbose_name_plural = "Настройки бота"

    def __str__(self) -> str:
        return "Настройки бота"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "BotConfig":
        obj, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return obj

    @property
    def is_paused(self) -> bool:
        return bool(self.paused_until and self.paused_until > timezone.now())


class Profile(models.Model):
    """Анкета, пришедшая от @leomatchbot."""

    STATUS_NEW = "new"
    STATUS_GENERATED = "generated"
    STATUS_SENT = "sent"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_NEW, "Новая"),
        (STATUS_GENERATED, "Текст сгенерирован"),
        (STATUS_SENT, "Отправлено"),
        (STATUS_SKIPPED, "Пропущена"),
        (STATUS_FAILED, "Ошибка"),
    ]

    chat_id = models.BigIntegerField("ID чата", db_index=True)
    message_id = models.BigIntegerField("ID сообщения", default=0)
    grouped_id = models.BigIntegerField("ID альбома", null=True, blank=True)

    name = models.CharField("Имя", max_length=128, blank=True, default="")
    age = models.PositiveSmallIntegerField("Возраст", null=True, blank=True)
    city = models.CharField("Город", max_length=128, blank=True, default="")
    about = models.TextField("Описание", blank=True, default="")
    raw_caption = models.TextField("Исходная подпись", blank=True, default="")

    language = models.CharField("Язык анкеты", max_length=8, blank=True, default="")
    photos = models.JSONField("Локальные пути к фото", default=list, blank=True)
    fingerprint = models.CharField("Отпечаток (для дедупликации)", max_length=64, db_index=True)

    status = models.CharField(
        "Статус", max_length=16, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True
    )
    skip_reason = models.CharField("Причина пропуска", max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Анкета"
        verbose_name_plural = "Анкеты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name or '???'}, {self.age or '?'}, {self.city or '?'}"

    @property
    def has_description(self) -> bool:
        return bool(self.about.strip())

    @staticmethod
    def make_fingerprint(name: str, age, city: str, about: str) -> str:
        raw = f"{(name or '').strip().lower()}|{age or ''}|{(city or '').strip().lower()}|{(about or '').strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


class GeneratedMessage(models.Model):
    """Текст, сгенерированный OpenAI для конкретной анкеты."""

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Черновик"),
        (STATUS_SENT, "Отправлено"),
        (STATUS_FAILED, "Ошибка отправки"),
    ]

    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="messages", verbose_name="Анкета"
    )
    text = models.TextField("Текст сообщения")
    language = models.CharField("Язык ответа", max_length=8, blank=True, default="")
    strategy = models.CharField(
        "Сценарий", max_length=32, blank=True, default=""
    )  # with_description / no_description
    model = models.CharField("Модель", max_length=64, blank=True, default="")
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)

    status = models.CharField(
        "Статус", max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True
    )
    error = models.TextField("Ошибка", blank=True, default="")
    sent_at = models.DateTimeField("Отправлено в", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Сгенерированное сообщение"
        verbose_name_plural = "Сгенерированные сообщения"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.text[:60]


class ActionLog(models.Model):
    """Лента событий юзербота — что он видел и что сделал."""

    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_ERROR = "error"

    level = models.CharField(max_length=8, default=LEVEL_INFO, db_index=True)
    event = models.CharField(max_length=64, db_index=True)
    detail = models.TextField(blank=True, default="")
    profile = models.ForeignKey(
        Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name="logs"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Событие"
        verbose_name_plural = "Журнал событий"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.level}] {self.event}"
