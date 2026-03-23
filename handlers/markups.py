from aiogram.utils.keyboard import (
    ReplyKeyboardBuilder,
    InlineKeyboardBuilder,
    InlineKeyboardMarkup,
)

import handlers.buttons as buttons
from utils.public_resources import TELEGRAM_CHANNEL_URL
from utils.translator import translator as ts

MAIN_MENU_REPLY_KEYBOARD = ReplyKeyboardBuilder()
MAIN_MENU_REPLY_KEYBOARD.button(
    text=ts.get("ru", "INSTALL_VPN_BUTTON"),
    style="success",
    icon_custom_emoji_id="5348239232852836489",
)
MAIN_MENU_REPLY_KEYBOARD.button(
    text=ts.get("ru", "MY_PROFILE_BUTTON"), icon_custom_emoji_id="5348223165380179822"
)
MAIN_MENU_REPLY_KEYBOARD.button(
    text=ts.get("ru", "TARIFFS_BUTTON"), icon_custom_emoji_id="5348503265967355284"
)
MAIN_MENU_REPLY_KEYBOARD.button(
    text=ts.get("ru", "QUESTIONS_BUTTON"), icon_custom_emoji_id="5348337458754894202"
)
MAIN_MENU_REPLY_KEYBOARD.button(
    text=ts.get("ru", "INVITE_FRIEND_BUTTON"),
    icon_custom_emoji_id="5283158435230147109",
)
MAIN_MENU_REPLY_KEYBOARD.adjust(2, 2, 1)


SELECT_YOUR_DEVICE_INLINE_KEYBOARD = InlineKeyboardBuilder()
SELECT_YOUR_DEVICE_INLINE_KEYBOARD.button(
    text=ts.get("ru", "ANDROID_BUTTON"),
    icon_custom_emoji_id="5373130604147654226",
    callback_data=ts.get("ru", "ANDROID_BUTTON"),
)
SELECT_YOUR_DEVICE_INLINE_KEYBOARD.button(
    text=ts.get("ru", "IOS_BUTTON"),
    icon_custom_emoji_id="5370722600668382252",
    callback_data=ts.get("ru", "IOS_BUTTON"),
)
SELECT_YOUR_DEVICE_INLINE_KEYBOARD.button(
    text=ts.get("ru", "WINDOWS_BUTTON"),
    icon_custom_emoji_id="5370857634440170316",
    callback_data=ts.get("ru", "WINDOWS_BUTTON"),
)
SELECT_YOUR_DEVICE_INLINE_KEYBOARD.button(
    text=ts.get("ru", "MACOS_BUTTON"),
    icon_custom_emoji_id="5370722600668382252",
    callback_data=ts.get("ru", "MACOS_BUTTON"),
)
SELECT_YOUR_DEVICE_INLINE_KEYBOARD.adjust(2, 2)


PROMO_SELECT_TARIFF_INLINE_KEYBOARD = InlineKeyboardBuilder()
PROMO_SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.THREE_DAYS_PROMO_TARIFF_BUTTON,
    callback_data=buttons.THREE_DAYS_PROMO_TARIFF_BUTTON,
    style="success",
    icon_custom_emoji_id="5361933723191231306",
)
PROMO_SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.ONE_MONTH_TARIFF_BUTTON,
    callback_data=buttons.ONE_MONTH_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
PROMO_SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.THREE_MONTHS_TARIFF_BUTTON,
    callback_data=buttons.THREE_MONTHS_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
PROMO_SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.ONE_YEAR_TARIFF_BUTTON,
    callback_data=buttons.ONE_YEAR_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
PROMO_SELECT_TARIFF_INLINE_KEYBOARD.adjust(1, 1, 1, 1, 1)

MY_PROFILE_INLINE_KEYBOARD = InlineKeyboardBuilder()
MY_PROFILE_INLINE_KEYBOARD.button(
    text=ts.get("ru", "SUBSCRIBE_ON_CHANNEL"),
    url=TELEGRAM_CHANNEL_URL,
    style="danger",
)

SELECT_TARIFF_INLINE_KEYBOARD = InlineKeyboardBuilder()
SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.ONE_DAY_TARIFF_BUTTON,
    callback_data=buttons.ONE_DAY_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.ONE_MONTH_TARIFF_BUTTON,
    callback_data=buttons.ONE_MONTH_TARIFF_BUTTON,
    style="success",
    icon_custom_emoji_id="5361933723191231306",
)
SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.THREE_MONTHS_TARIFF_BUTTON,
    callback_data=buttons.THREE_MONTHS_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
SELECT_TARIFF_INLINE_KEYBOARD.button(
    text=buttons.ONE_YEAR_TARIFF_BUTTON,
    callback_data=buttons.ONE_YEAR_TARIFF_BUTTON,
    icon_custom_emoji_id="5361933723191231306",
)
SELECT_TARIFF_INLINE_KEYBOARD.adjust(1, 1, 1, 1, 1)


QUESTIONS_INLINE_KEYBOARD = InlineKeyboardBuilder()
QUESTIONS_INLINE_KEYBOARD.button(
    text=ts.get("ru", "NO_WL_BUTTON"),
    callback_data=ts.get("ru", "NO_WL_BUTTON"),
)
QUESTIONS_INLINE_KEYBOARD.button(
    text=ts.get("ru", "VPN_DOESNT_WORK_BUTTON"),
    callback_data=ts.get("ru", "VPN_DOESNT_WORK_BUTTON"),
)
QUESTIONS_INLINE_KEYBOARD.button(
    text=ts.get("ru", "CANCEL_SUBSCRIPTION_BUTTON"),
    callback_data=ts.get("ru", "CANCEL_SUBSCRIPTION_BUTTON"),
)
QUESTIONS_INLINE_KEYBOARD.button(
    text=ts.get("ru", "BLOCK_ADULT_WEBSITES_BUTTON"),
    callback_data=ts.get("ru", "BLOCK_ADULT_WEBSITES_BUTTON"),
)
QUESTIONS_INLINE_KEYBOARD.adjust(1, 1, 1, 1)


BACK_TO_QUESTIONS_INLINE_KEYBOARD = InlineKeyboardBuilder()
BACK_TO_QUESTIONS_INLINE_KEYBOARD.button(
    text=ts.get("ru", "BACK_TO_QUESTIONS_BUTTON"),
    callback_data=buttons.BACK_TO_QUESTIONS_BUTTON_DATA,
)
BACK_TO_QUESTIONS_INLINE_KEYBOARD.adjust(1)


CANCEL_AUTOPAY_INLINE_KEYBOARD = InlineKeyboardBuilder()
CANCEL_AUTOPAY_INLINE_KEYBOARD.button(
    text=ts.get("ru", "SAVE_RECURRENT_PAYMENT_BUTTON"),
    callback_data=ts.get("ru", "SAVE_RECURRENT_PAYMENT_BUTTON"),
    style="success",
)
CANCEL_AUTOPAY_INLINE_KEYBOARD.button(
    text=ts.get("ru", "CANCEL_RECURRENT_PAYMENT_BUTTON"),
    callback_data=ts.get("ru", "CANCEL_RECURRENT_PAYMENT_BUTTON"),
    style="danger",
)
CANCEL_AUTOPAY_INLINE_KEYBOARD.adjust(2)


def create_one_click_connect_keyboard(url: str) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.button(
        text=ts.get("ru", "ONE_CLICK_INSTALL_BUTTON"),
        url=url,
        style="success",
        icon_custom_emoji_id="5348239232852836489",
    )
    keyboard.adjust(1)
    return keyboard.as_markup()
