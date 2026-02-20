import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)


class GlobalErrorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            return await self._handle_telegram_error(event, e)
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка в GlobalErrorMiddleware: {e}", exc_info=True
            )
            raise

    async def _handle_telegram_error(
        self, event: TelegramObject, error: TelegramBadRequest
    ):
        error_message = str(error).lower()

        if self._is_old_query_error(error_message):
            return await self._handle_old_query(event, error)
        if self._is_message_not_modified_error(error_message):
            return await self._handle_message_not_modified(event, error)
        if self._is_topic_required_error(error_message):
            # Канал с топиками — просто игнорируем
            logger.debug(
                f"📋 [GlobalErrorMiddleware] Игнорируем ошибку топика: {error}"
            )
            return None
        if self._is_bad_request_error(error_message):
            return await self._handle_bad_request(event, error)
        logger.error(f"Неизвестная Telegram API ошибка: {error}")
        raise error

    def _is_old_query_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in [
                "query is too old",
                "query id is invalid",
                "response timeout expired",
            ]
        )

    def _is_message_not_modified_error(self, error_message: str) -> bool:
        return "message is not modified" in error_message

    def _is_bad_request_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in [
                "message not found",
                "chat not found",
                "bot was blocked by the user",
                "user is deactivated",
            ]
        )

    def _is_topic_required_error(self, error_message: str) -> bool:
        return any(
            phrase in error_message
            for phrase in [
                "topic must be specified",
                "topic_closed",
                "topic_deleted",
                "forum_closed",
            ]
        )

    async def _handle_old_query(self, event: TelegramObject, error: TelegramBadRequest):
        if isinstance(event, CallbackQuery):
            user_info = self._get_user_info(event)
            logger.warning(
                f"🕐 [GlobalErrorMiddleware] Игнорируем устаревший callback '{event.data}' от {user_info}"
            )
        else:
            logger.warning(
                f"🕐 [GlobalErrorMiddleware] Игнорируем устаревший запрос: {error}"
            )

    async def _handle_message_not_modified(
        self, event: TelegramObject, error: TelegramBadRequest
    ):
        logger.debug(f"📝 [GlobalErrorMiddleware] Сообщение не было изменено: {error}")

        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
                logger.debug(
                    "✅ Успешно ответили на callback после 'message not modified'"
                )
            except TelegramBadRequest as answer_error:
                if not self._is_old_query_error(str(answer_error).lower()):
                    logger.error(f"❌ Ошибка при ответе на callback: {answer_error}")

    async def _handle_bad_request(
        self, event: TelegramObject, error: TelegramBadRequest
    ):
        error_message = str(error).lower()

        if "bot was blocked" in error_message:
            user_info = (
                self._get_user_info(event) if hasattr(event, "from_user") else "Unknown"
            )
            logger.info(
                f"🚫 [GlobalErrorMiddleware] Бот заблокирован пользователем {user_info}"
            )
            return
        if "user is deactivated" in error_message:
            user_info = (
                self._get_user_info(event) if hasattr(event, "from_user") else "Unknown"
            )
            logger.info(
                f"👻 [GlobalErrorMiddleware] Пользователь деактивирован {user_info}"
            )
            return
        if "chat not found" in error_message or "message not found" in error_message:
            logger.warning(
                f"🔍 [GlobalErrorMiddleware] Чат или сообщение не найдено: {error}"
            )
            return
        logger.error(
            f"❌ [GlobalErrorMiddleware] Неизвестная bad request ошибка: {error}"
        )
        raise error

    def _get_user_info(self, event: TelegramObject) -> str:
        if hasattr(event, "from_user") and event.from_user:
            if event.from_user.username:
                return f"@{event.from_user.username}"
            return f"ID:{event.from_user.id}"
        return "Unknown"


class ErrorStatisticsMiddleware(BaseMiddleware):
    def __init__(self):
        self.error_counts = {
            "old_queries": 0,
            "message_not_modified": 0,
            "bot_blocked": 0,
            "user_deactivated": 0,
            "other_errors": 0,
        }

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as e:
            self._count_error(e)
            raise

    def _count_error(self, error: TelegramBadRequest):
        error_message = str(error).lower()

        if "query is too old" in error_message:
            self.error_counts["old_queries"] += 1
        elif "message is not modified" in error_message:
            self.error_counts["message_not_modified"] += 1
        elif "bot was blocked" in error_message:
            self.error_counts["bot_blocked"] += 1
        elif "user is deactivated" in error_message:
            self.error_counts["user_deactivated"] += 1
        else:
            self.error_counts["other_errors"] += 1

    def get_statistics(self) -> dict:
        return self.error_counts.copy()

    def reset_statistics(self):
        for key in self.error_counts:
            self.error_counts[key] = 0
