"""Разбор сообщений Дайвинчика: анкеты + служебные фразы."""
from __future__ import annotations

import re
import unicodedata

AGE_MIN, AGE_MAX = 14, 99

# Разделитель между «шапкой» (имя, возраст, город) и описанием.
_DASH_SEP = re.compile(r"\s+[–—−-]\s+|\s*[–—−]\s*")

_UZ_MARKERS = {
    "men", "sen", "siz", "bilan", "uchun", "kerak", "yaxshi", "qiz", "yigit",
    "hayot", "sevgi", "dost", "salom", "shahar", "yosh", "juda", "emas",
    "bo'lsa", "bolsa", "qilaman", "yoqadi", "haqida", "istayman", "tanishmoq",
    "tanishaman", "odam", "ish", "oila", "qalbim", "mehr",
}
_UZ_CYR_CHARS = set("ўқғҳ")


def detect_language(text: str) -> str:
    """Очень лёгкий детектор: ru / uz / en / '' (не определено)."""
    if not text or not text.strip():
        return ""
    lowered = text.lower()
    cyr = sum(1 for ch in lowered if "а" <= ch <= "я" or ch in "ёўқғҳ")
    lat = sum(1 for ch in lowered if "a" <= ch <= "z")
    if cyr == 0 and lat == 0:
        return ""
    if cyr >= lat:
        if _UZ_CYR_CHARS & set(lowered):
            return "uz"
        return "ru"
    words = set(re.findall(r"[a-z']+", lowered))
    if words & _UZ_MARKERS or "ʻ" in text or "o'" in lowered or "g'" in lowered:
        return "uz"
    return "en"


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return value.strip(" \t\n\r,;·•")


def split_header_about(caption: str) -> tuple[str, str]:
    """Делит подпись на «шапку» и описание."""
    caption = (caption or "").replace("\r\n", "\n").strip()
    if not caption:
        return "", ""

    first_line, _, tail = caption.partition("\n")
    match = _DASH_SEP.search(first_line)
    if match:
        header = first_line[: match.start()]
        about = first_line[match.end():]
        if tail:
            about = f"{about}\n{tail}".strip()
        return header, about
    return first_line, tail.strip()


def parse_profile_caption(caption: str) -> dict | None:
    """Разбирает подпись анкеты вида «Имя, 24, Ташкент – описание».

    Возвращает dict(name, age, city, about, raw_caption, language) или None,
    если это явно не анкета.
    """
    caption = (caption or "").strip()
    if not caption:
        return None

    header, about = split_header_about(caption)
    parts = [p.strip() for p in header.split(",")]

    age = None
    age_index = -1
    for index, part in enumerate(parts):
        digits = re.fullmatch(r"(\d{1,2})", part)
        if digits and AGE_MIN <= int(digits.group(1)) <= AGE_MAX:
            age = int(digits.group(1))
            age_index = index
            break

    if age is None:
        # Иногда возраст приклеен к городу: «Аня 24 Ташкент»
        digits = re.search(r"\b(\d{2})\b", header)
        if not digits or not (AGE_MIN <= int(digits.group(1)) <= AGE_MAX):
            return None
        age = int(digits.group(1))
        name = _clean(header[: digits.start()])
        city = _clean(header[digits.end():])
    else:
        name = _clean(", ".join(parts[:age_index]))
        city = _clean(", ".join(parts[age_index + 1:]))

    about = _clean(about)
    return {
        "name": name,
        "age": age,
        "city": city,
        "about": about,
        "raw_caption": caption,
        "language": detect_language(about) or detect_language(f"{name} {city}"),
    }


# ----------------------------------------------------------- служебные фразы --
ASK_MESSAGE_PATTERNS = (
    "отправь текст",
    "напиши сообщение",
    "отправь сообщение",
    "видео или голосовое",
)
LIKE_SENT_PATTERNS = ("лайк отправлен", "ждем ответа", "ждём ответа")
MENU_PATTERNS = ("смотреть анкеты", "заполнить анкету заново", "изменить фото")
NO_MORE_PATTERNS = (
    "анкеты закончились",
    "на сегодня всё",
    "больше нет анкет",
    "новых анкет пока нет",
    "попробуй позже",
)
MUTUAL_PATTERNS = ("есть взаимная симпатия", "твоя симпатия взаимна", "вы понравились друг другу")
LIMIT_PATTERNS = ("слишком часто", "подожди", "лимит")
CONFIRM_SEND_PATTERNS = ("отправить это сообщение", "отправить сообщение пользователю")
LANGUAGE_PROMPT_PATTERNS = ("o'zbek tili", "o‘zbek tili", "o'tasanmi", "tiliga o'tish")


def classify_bot_message(text: str) -> str:
    """Определяет тип служебного сообщения Дайвинчика."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return "unknown"
    if any(p in lowered for p in CONFIRM_SEND_PATTERNS):
        return "confirm_send"
    if any(p in lowered for p in LANGUAGE_PROMPT_PATTERNS):
        return "language_prompt"
    if any(p in lowered for p in ASK_MESSAGE_PATTERNS):
        return "ask_message"
    if any(p in lowered for p in LIKE_SENT_PATTERNS):
        return "like_sent"
    if any(p in lowered for p in MUTUAL_PATTERNS):
        return "mutual"
    if any(p in lowered for p in NO_MORE_PATTERNS):
        return "no_more"
    if any(p in lowered for p in MENU_PATTERNS):
        return "menu"
    if any(p in lowered for p in LIMIT_PATTERNS):
        return "limit"
    return "unknown"


# Кнопки, ведущие к трате денег или звёзд. Скрипт не нажимает их ни при каких условиях.
PAID_BUTTON_PATTERNS = (
    "⭐",
    "premium",
    "оплат",
    "купить",
    "куплю",
    "тариф",
    "донат",
    "₽",
    "$",
    "sotib",
    "to'lov",
)


def is_paid_button(text: str) -> bool:
    """Кнопка, за которой стоят деньги или звёзды Telegram."""
    lowered = (text or "").lower()
    return any(pattern in lowered for pattern in PAID_BUTTON_PATTERNS)


