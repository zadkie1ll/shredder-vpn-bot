import asyncio
import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, Message
from aiogram.types import InlineKeyboardMarkup

from filters.is_admin import IsAdmin
from services.messages_catalog import (
    MessagePreview,
    build_action_control_reward_menu,
    build_message_previews,
)
from utils.config import Config

messages_router = Router()
PREVIEW_CALLBACK = "messages_preview_noop"


def _preview_markup(preview: MessagePreview) -> InlineKeyboardMarkup | None:
    if not preview.buttons:
        return None
    keyboard = []
    for row_index, row in enumerate(preview.buttons):
        keyboard_row = []
        for button_index, label in enumerate(row):
            callback_data = PREVIEW_CALLBACK
            if row_index < len(preview.button_callbacks):
                callbacks_row = preview.button_callbacks[row_index]
                if button_index < len(callbacks_row):
                    callback_data = callbacks_row[button_index]
            keyboard_row.append(
                InlineKeyboardButton(text=label, callback_data=callback_data)
            )
        keyboard.append(keyboard_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _send_preview(message: Message, preview: MessagePreview) -> None:
    text = preview.text
    markup = _preview_markup(preview)
    while True:
        try:
            if preview.photo and preview.photo.exists():
                await message.answer_photo(
                    photo=FSInputFile(preview.photo),
                    caption=text,
                    reply_markup=markup,
                )
            else:
                await message.answer(text, reply_markup=markup)
            return
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramBadRequest as exc:
            logging.warning(
                "Could not render messages preview %s as HTML: %s",
                preview.sources,
                exc,
            )
            fallback = (
                preview.text
            )
            await message.answer(fallback, reply_markup=markup, parse_mode=None)
            return


@messages_router.message(Command("messages"), IsAdmin())
async def send_messages_catalog(message: Message, config: Config) -> None:
    previews = build_message_previews(config)
    await message.answer(
        f"Начинаю предпросмотр <b>{len(previews)}</b> сообщений. "
        "Кнопки безопасны и не выполняют реальные действия."
    )

    sent = 0
    failed = 0
    for preview in previews:
        try:
            await _send_preview(message, preview)
            sent += 1
        except Exception:
            failed += 1
            logging.exception("Failed to send messages preview %s", preview.sources)
        await asyncio.sleep(0.15)

    await message.answer(
        f"Предпросмотр завершён. Отправлено: <b>{sent}</b>, ошибок: <b>{failed}</b>."
    )


@messages_router.callback_query(F.data == PREVIEW_CALLBACK)
async def ignore_preview_button(query: CallbackQuery) -> None:
    await query.answer("Это безопасная кнопка предпросмотра.")


@messages_router.callback_query(F.data.startswith("messages_action:"), IsAdmin())
async def show_action_control_reward_menu(query: CallbackQuery) -> None:
    action_key = query.data.removeprefix("messages_action:")
    preview = build_action_control_reward_menu(action_key)
    if preview is None or query.message is None:
        await query.answer("Меню тарифов не найдено", show_alert=True)
        return
    await query.message.answer(
        preview.text,
        reply_markup=_preview_markup(preview),
    )
    await query.answer()
