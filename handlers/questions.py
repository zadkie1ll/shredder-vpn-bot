import logging
from aiogram import F
from aiogram import Router
from aiogram.types import CallbackQuery

import handlers.buttons as buttons
import handlers.markups as markups
from common.rwms_client import RwmsClient
from utils.encrypt_happ_url import encrypt_happ_url
from .misc import log_function_name
from .misc import send_typing_action
from utils.translator import translator as ts

questions_router = Router()


# Кнопка "Нет белых списков" в разделе "Вопросы"
@questions_router.callback_query(F.data.startswith(ts.get("ru", "NO_WL_BUTTON")))
@log_function_name
@send_typing_action
async def __no_wl_question_clicked(query: CallbackQuery):
    await query.message.edit_text(
        text=ts.get("ru", "NO_WL_ANSWER"),
        reply_markup=markups.BACK_TO_QUESTIONS_INLINE_KEYBOARD.as_markup(),
    )


# Кнопка "Нет интернета при подключении" в разделе "Вопросы"
@questions_router.callback_query(
    F.data.startswith(ts.get("ru", "VPN_DOESNT_WORK_BUTTON"))
)
@log_function_name
@send_typing_action
async def __vpn_doesnt_work_question_clicked(query: CallbackQuery):
    await query.message.edit_text(
        text=ts.get("ru", "VPN_DOEST_WORK_ANSWER"),
        reply_markup=markups.BACK_TO_QUESTIONS_INLINE_KEYBOARD.as_markup(),
        disable_web_page_preview=True,
    )


# Кнопка "Отменить подписку" в разделе "Вопросы"
@questions_router.callback_query(
    F.data.startswith(ts.get("ru", "CANCEL_SUBSCRIPTION_BUTTON"))
)
@log_function_name
@send_typing_action
async def __cancel_subscription_question_clicked(query: CallbackQuery):
    await query.message.edit_text(
        text=ts.get("ru", "HOW_TO_CANCEL_SUBSCRIPTION_ANSWER"),
        reply_markup=markups.BACK_TO_QUESTIONS_INLINE_KEYBOARD.as_markup(),
    )


# Кнопка "Блокировка сайтов для взрослых" в разделе "Вопросы"
@questions_router.callback_query(
    F.data.startswith(ts.get("ru", "BLOCK_ADULT_WEBSITES_BUTTON"))
)
@log_function_name
@send_typing_action
async def __block_adult_websites_question_clicked(
    query: CallbackQuery, rwms_client: RwmsClient
):
    user = await rwms_client.get_user_by_username(username=str(query.from_user.id))

    if user is None:
        logging.error(f"User {query.from_user.id} not found")
        return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

    encrypted_happ_url = (
        f"happ://crypt3/{encrypt_happ_url(user.subscription_url + "/np")}"
    )

    await query.message.edit_text(
        text=ts.get("ru", "BLOCK_ADULT_WEBSITES_ANSWER", encrypted_happ_url),
        reply_markup=markups.BACK_TO_QUESTIONS_INLINE_KEYBOARD.as_markup(),
        disable_web_page_preview=True,
    )


# Кнопка "Назад" в разделе "Вопросы"
@questions_router.callback_query(
    F.data.startswith(buttons.BACK_TO_QUESTIONS_BUTTON_DATA)
)
@log_function_name
@send_typing_action
async def __back_to_question_clicked(query: CallbackQuery):
    await query.message.edit_text(
        text=ts.get("ru", "QUESTIONS"),
        reply_markup=markups.QUESTIONS_INLINE_KEYBOARD.as_markup(),
    )
