import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
    TelegramObject,
    User as TgUser,
)

from utils.translator import translator as ts

logger = logging.getLogger(__name__)


ZERO_WIDTH_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF]")

LINK_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"t\.me/\+",
        r"joinchat",
        r"https?://",
        r"www\.",
        r"tg://",
        r"telegram\.me",
        r"t\.me",
    )
]

DOMAIN_OBFUSCATION_PATTERN = re.compile(
    r"(?<![0-9a-zа-яё])(?:t|т)[\s\W_]*?(?:m|м)(?:e|е)",
    re.IGNORECASE,
)

CHAR_TRANSLATION = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "к": "k",
        "т": "t",
        "г": "g",
        "м": "m",
        "н": "n",
        "л": "l",
        "і": "i",
        "ї": "i",
        "ё": "e",
        "ь": "",
        "ъ": "",
        "ў": "u",
        "＠": "@",
    }
)

COLLAPSE_PATTERN = re.compile(r"[\s\._\-/\\|,:;•·﹒․⋅··`~'\"!?()\[\]{}<>+=]+")


class DisplayNameRestrictionMiddleware(BaseMiddleware):
    """Blocks users whose display name imitates links or official accounts."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: TgUser | None = None

        if isinstance(event, (Message, CallbackQuery, PreCheckoutQuery)):
            user = event.from_user

        if not user or user.is_bot:
            return await handler(event, data)

        display_name = self._build_display_name(user)
        username = user.username or ""

        display_suspicious = self._is_suspicious(display_name)
        username_suspicious = self._is_suspicious(username)

        if display_suspicious or username_suspicious:
            suspicious_value = display_name if display_suspicious else username
            warning = ts.get("ru", "SUSPICIOUS_DISPLAY_NAME_BLOCKED")

            logger.warning(
                "🚫 DisplayNameRestriction: user %s blocked due to suspicious name '%s'",
                user.id,
                suspicious_value,
            )

            if isinstance(event, Message):
                await event.answer(warning)
            elif isinstance(event, CallbackQuery):
                await event.answer(warning, show_alert=True)
            elif isinstance(event, PreCheckoutQuery):
                await event.answer(ok=False, error_message=warning)
            return None

        return await handler(event, data)

    @staticmethod
    def _build_display_name(user: TgUser) -> str:
        parts = [user.first_name or "", user.last_name or ""]
        return " ".join(part for part in parts if part).strip()

    def _is_suspicious(self, value: str) -> bool:
        if not value:
            return False

        cleaned = ZERO_WIDTH_PATTERN.sub("", value)
        lower_value = cleaned.lower()

        if "@" in cleaned or "＠" in cleaned:
            return True

        if any(pattern.search(lower_value) for pattern in LINK_PATTERNS):
            return True

        # Проверяем обфусцированные ссылки типа "t . m e" или "т м е"
        # Но НЕ блокируем если это часть обычного слова/имени
        domain_match = DOMAIN_OBFUSCATION_PATTERN.search(lower_value)
        if domain_match:
            # Проверяем контекст: если "tme" внутри слова (с буквами с обеих сторон) - пропускаем
            start_pos = domain_match.start()
            end_pos = domain_match.end()

            # Проверяем символ ДО и ПОСЛЕ совпадения
            has_letter_before = start_pos > 0 and lower_value[start_pos - 1].isalpha()
            has_letter_after = (
                end_pos < len(lower_value) and lower_value[end_pos].isalpha()
            )

            # Если с ОБЕИХ сторон буквы - скорее всего это просто имя/фамилия
            if not (has_letter_before and has_letter_after):
                return True

        normalized = self._normalize_text(lower_value)
        collapsed = COLLAPSE_PATTERN.sub("", normalized)

        # Проверяем "tme" с контекстом (ловим t.me ссылки, но не случайные совпадения в именах)
        # Ищем tme в начале, конце, или с пробелами/спецсимволами вокруг
        if re.search(r"(?:^|[^a-zа-яё])tme(?:[^a-zа-яё]|$)", collapsed, re.IGNORECASE):
            return True

        return False

    @staticmethod
    def _normalize_text(value: str) -> str:
        return value.translate(CHAR_TRANSLATION)
