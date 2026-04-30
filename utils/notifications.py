import logging
import random
import asyncio
import handlers.markups as markups
from typing import Optional
from random import randint
from aiogram import Bot
from aiogram.types import ReplyMarkupUnion
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy.ext.asyncio import async_sessionmaker
from common.models.tariff import TrialPromotionTariff
from common.models.tariff import str_to_tariff
from common.models.tariff import tariff_to_human_str
from common.models.messages import NotificateUserMessage
from common.models.messages import ReferralPurchaseBonusApplied
from common.models.messages import ReferralReachedTrafficBonusApplied
from utils.redis_message_broker import RedisMessageBroker
from utils.translator import translator as ts

from utils.sql_helpers import (
    has_payment_for_user_by_tg_id,
    save_notified_expired_user,
    save_notified_nc_user,
    save_notified_three_days_left_user,
    save_notified_one_day_left_user,
)

sub_expired_promo_msgs = [
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO1"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO2"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO3"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO4"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO5"),
]

nc_msgs = [
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED1"),
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED2"),
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED3"),
]

NOTIFICATION_CONFIG = {
    "subscription-expired": {
        "promo": sub_expired_promo_msgs,
        "regular": ts.get("ru", "NOTIFY_EXPIRED_USER"),
        "save_func": save_notified_expired_user,
    },
    "3-days-left": {
        "promo": ts.get("ru", "NOTIFY_THREE_DAYS_LEFT_PROMO"),
        "regular": ts.get("ru", "NOTIFY_THREE_DAYS_LEFT"),
        "save_func": save_notified_three_days_left_user,
    },
    "1-day-left": {
        "promo": ts.get("ru", "NOTIFY_ONE_DAY_LEFT_PROMO"),
        "regular": ts.get("ru", "NOTIFY_ONE_DAY_LEFT"),
        "save_func": save_notified_one_day_left_user,
    },
    "nc-yesterday-created": {
        "random_list": nc_msgs,
        "save_func": save_notified_nc_user,
    },
    "purchase-success-non-autopay": {
        "static": ts.get("ru", "NOTIFY_SUCCESSFUL_NON_AUTOPAY")
    },
    "purchase-failure-autopay": {"static": ts.get("ru", "NOTIFY_AUTOPAY_FAILURE")},
    "purchase-failure-non-autopay": {
        "static": ts.get("ru", "NOTIFY_NON_AUTOPAY_FAILURE")
    },
    "referral_traffic_reached_bonus_applied": {
        "static": ts.get("ru", "NOTIFY_REFERRAL_TRAFFIC_REACHED_BONUS")
    },
    "referral_purchase_bonus_applied": {
        "static": ts.get("ru", "NOTIFY_REFERRAL_PURCHASE_BONUS_APPLIED")
    },
}


def format_trial_promo_text(text: str) -> str:
    if "{}" not in text:
        return text

    return text.format(TrialPromotionTariff().price)


def pluralize_ru(count: int, forms: tuple[str, str, str]) -> str:
    """
    Возвращает правильную форму слова для русского языка
    forms: (один, два-четыре, много)
    пример: ("друг", "друга", "друзей")
    """
    if count % 10 == 1 and count % 100 != 11:
        return forms[0]
    elif 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
        return forms[1]
    else:
        return forms[2]


async def safe_send_message(
    bot: Bot, chat_id: int, text: str, markup: Optional[ReplyMarkupUnion]
) -> bool:
    """
    Отправляет сообщение пользователю и возвращает True/False.
    True — сообщение доставлено ИЛИ ошибка, при которой нельзя ничего сделать (chat not found, bot blocked).
    False — ошибка, из-за которой можно попробовать повторить отправку.
    """
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        logging.info(f"message sent to {chat_id}")
        return True
    except TelegramRetryAfter as e:
        logging.warning(
            f"got TelegramRetryAfter for {chat_id}, sleep {e.retry_after} seconds"
        )

        await asyncio.sleep(e.retry_after + 1)
        return await safe_send_message(bot, chat_id, text, markup)
    except TelegramForbiddenError as e:
        # Бот заблокирован пользователем
        logging.warning(f"can't send message to {chat_id}: bot was blocked ({e})")
        return True
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            logging.warning(f"can't send message to {chat_id}: chat not found ({e})")
            return True
        logging.error(f"bad request while sending message to {chat_id}: {e}")
        return False
    except Exception as e:
        logging.exception(f"unexpected error sending message to {chat_id}: {e}")
        return False


async def process_notification(
    bot: Bot,
    session_maker: async_sessionmaker,
    message: NotificateUserMessage,
) -> None:
    telegram_id = message.telegram_id
    notification_type = message.notification_type

    config = NOTIFICATION_CONFIG.get(notification_type)
    if not config:
        logging.warning(f"unknown notification type: {notification_type}")
        return

    text_to_send = None
    markup = None

    if "static" in config:
        text_to_send = config["static"]

        if isinstance(message, ReferralReachedTrafficBonusApplied):
            friend_forms = (
                f"{message.referral_reached_traffic_count} ваш друг стал",
                f"{message.referral_reached_traffic_count} ваших друга стали",
                f"{message.referral_reached_traffic_count} ваших друзей стали",
            )

            form = pluralize_ru(message.referral_reached_traffic_count, friend_forms)
            text_to_send = text_to_send.format(form, message.bonus_days_count)

        if isinstance(message, ReferralPurchaseBonusApplied):
            tariff = str_to_tariff(message.referral_tariff)
            tariff_name = tariff_to_human_str(tariff)

            if tariff_name is not None:
                text_to_send = config["static"].format(
                    tariff_name, message.bonus_days_count
                )

    elif "random_list" in config:
        text_to_send = config["random_list"][randint(0, len(config["random_list"]) - 1)]
        markup = markups.SELECT_YOUR_DEVICE_INLINE_KEYBOARD.as_markup()
    else:
        async with session_maker() as session:
            has_payment = await has_payment_for_user_by_tg_id(
                session=session, telegram_id=telegram_id
            )
            if isinstance(config["promo"], list):
                text_to_send = (
                    config["promo"][randint(0, len(config["promo"]) - 1)]
                    if not has_payment
                    else config["regular"]
                )
            else:
                text_to_send = config["promo"] if not has_payment else config["regular"]

            if not has_payment:
                text_to_send = format_trial_promo_text(text_to_send)

            if has_payment:
                markup = markups.SELECT_TARIFF_INLINE_KEYBOARD.as_markup()
            else:
                markup = markups.PROMO_SELECT_TARIFF_INLINE_KEYBOARD.as_markup()

    logging.info(f"sending notification '{notification_type}' to user {telegram_id}")
    notified = await safe_send_message(
        bot=bot, chat_id=telegram_id, text=text_to_send, markup=markup
    )

    if notified:
        logging.info(
            f"notification '{notification_type}' for user {telegram_id} handled"
        )

    if notified and "save_func" in config:
        async with session_maker() as session:
            async with session.begin():
                await config["save_func"](session=session, telegram_id=telegram_id)
        logging.debug(
            f"saved notification record for user {telegram_id} and type '{notification_type}'"
        )


async def listen_notifications(
    bot: Bot,
    redis_message_broker: RedisMessageBroker,
    session_maker: async_sessionmaker,
):
    while True:
        try:
            message = await redis_message_broker.pop_message(timeout=5)

            if message is None:
                logging.debug("no messages in the redis queue, waiting...")
                continue

            if isinstance(message, NotificateUserMessage):
                logging.debug(
                    f"received message for user {message.telegram_id} with notification type '{message.notification_type}'"
                )

                if message.notification_type == "purchase-success-autopay":
                    logging.debug(
                        "skipping notification type 'purchase-success-autopay'"
                    )
                    continue

                await process_notification(bot, session_maker, message)
                continue

            logging.warning(f"invalid message type: {message}")
            await asyncio.sleep(random.uniform(0.5, 1))

        except Exception:
            logging.exception(f"notifying error")
