import logging
import random
import asyncio
import handlers.markups as markups
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from random import randint
from aiogram import Bot
from aiogram.types import ReplyMarkupUnion
from aiogram.exceptions import TelegramBadRequest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy.ext.asyncio import async_sessionmaker
from common.rwms_client import RwmsClient
from utils.config import Config
from common.models.tariff import TrialPromotionTariff
from common.models.tariff import str_to_tariff
from common.models.tariff import tariff_to_human_str
from common.models.messages import NotificateUserMessage
from common.models.messages import ReferralPurchaseBonusApplied
from common.models.messages import ReferralReachedTrafficBonusApplied
from utils.referral_rewards import award_sales_referral_purchase_bonus
from utils.redis_message_broker import RedisMessageBroker
from texts.notifications import NOTIFICATION_CONFIG, SILENT_NOTIFICATION_TYPES
from utils.translator import translator as ts
from utils.sql_helpers import tx
from utils.sql_helpers import get_user_bot_instance

from utils.sql_helpers import (
    get_latest_successful_payment_by_tg_id,
    has_payment_for_user_by_tg_id,
)

# "Предложения купить", отправленные проактивно (без действия юзера) — по ним
# считаем конверсию: отправили -> купил или нет. Обычные приветствия, кнопки в
# боте и рефералка сюда не входят — так решил старший разраб.
OFFER_NOTIFICATION_TYPES = {
    "subscription-expired",
    "3-days-left",
    "1-day-left",
    "nc-yesterday-created",
}

# Сигнал "юзер купил" — если для него есть запомненное предложение, отмечаем
# конверсию. purchase-success-autopay сюда входит, даже если сообщение по нему
# не шлётся (см. пропуск ниже в listen_notifications).
SUCCESS_NOTIFICATION_TYPES = {
    "purchase-success-autopay",
    "purchase-success-non-autopay",
}

PENDING_CONVERSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 дней
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DAILY_REPORT_HOUR = 19
DAILY_REPORT_MINUTE = 0


@dataclass(frozen=True)
class SendResult:
    handled: bool
    sent: bool
    status: str


def has_telegram_recipient(message: NotificateUserMessage) -> bool:
    return message.telegram_id is not None and message.telegram_id > 0


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
) -> SendResult:
    """
    Отправляет сообщение пользователю.
    handled=True означает, что сообщение не надо возвращать в очередь.
    sent=True означает, что Telegram реально принял сообщение.
    """
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        logging.info(f"message sent to {chat_id}")
        return SendResult(handled=True, sent=True, status="sent")
    except TelegramRetryAfter as e:
        logging.warning(
            f"got TelegramRetryAfter for {chat_id}, sleep {e.retry_after} seconds"
        )

        await asyncio.sleep(e.retry_after + 1)
        return await safe_send_message(bot, chat_id, text, markup)
    except TelegramForbiddenError as e:
        # Бот заблокирован пользователем
        logging.warning(f"can't send message to {chat_id}: bot was blocked ({e})")
        return SendResult(handled=True, sent=False, status="undeliverable")
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            logging.warning(f"can't send message to {chat_id}: chat not found ({e})")
            return SendResult(handled=True, sent=False, status="undeliverable")
        logging.error(f"bad request while sending message to {chat_id}: {e}")
        return SendResult(handled=False, sent=False, status="failed")
    except Exception as e:
        logging.exception(f"unexpected error sending message to {chat_id}: {e}")
        return SendResult(handled=False, sent=False, status="failed")


async def process_notification(
    bot: Bot,
    session_maker: async_sessionmaker,
    message: NotificateUserMessage,
    redis_message_broker: RedisMessageBroker | None = None,
) -> str:
    if not has_telegram_recipient(message):
        logging.info(
            "Skipping notification '%s' because no Telegram account is linked",
            message.notification_type,
        )
        return "skipped_no_recipient"

    telegram_id = message.telegram_id
    notification_type = message.notification_type

    if notification_type in SILENT_NOTIFICATION_TYPES:
        logging.info(
            "Skipping silent notification '%s' for user %s",
            notification_type,
            telegram_id,
        )
        return "skipped_silent"

    config = NOTIFICATION_CONFIG.get(notification_type)
    if not config:
        logging.warning(f"unknown notification type: {notification_type}")
        return "unknown_type"

    text_to_send = None
    markup = None

    if "static_key" in config:
        text_to_send = ts.get("ru", config["static_key"])

        if notification_type == "purchase-success-non-autopay":
            async with session_maker() as session:
                payment = await get_latest_successful_payment_by_tg_id(
                    session=session, telegram_id=telegram_id
                )

            tariff = (
                str_to_tariff(payment.subscription_period)
                if payment is not None
                else None
            )
            if tariff is not None:
                days = tariff.subscription_period.days
                days_word = pluralize_ru(days, ("день", "дня", "дней"))
                text_to_send = text_to_send.format(days=days, days_word=days_word)
            else:
                text_to_send = ts.get("ru", config["fallback_key"])

        if isinstance(message, ReferralReachedTrafficBonusApplied):
            friend_forms = (
                f"{message.referral_reached_traffic_count} ваш друг использовал",
                f"{message.referral_reached_traffic_count} ваших друга использовали",
                f"{message.referral_reached_traffic_count} ваших друзей использовали",
            )

            form = pluralize_ru(message.referral_reached_traffic_count, friend_forms)
            text_to_send = text_to_send.format(form, message.bonus_days_count)

        if isinstance(message, ReferralPurchaseBonusApplied):
            tariff = str_to_tariff(message.referral_tariff)
            tariff_name = tariff_to_human_str(tariff)

            if tariff_name is not None:
                text_to_send = ts.get("ru", config["static_key"]).format(
                    tariff_name, message.bonus_days_count
                )

    elif "random_keys" in config:
        key = config["random_keys"][randint(0, len(config["random_keys"]) - 1)]
        text_to_send = ts.get("ru", key)
        markup = markups.SELECT_YOUR_DEVICE_INLINE_KEYBOARD.as_markup()
    else:
        async with session_maker() as session:
            has_payment = await has_payment_for_user_by_tg_id(
                session=session, telegram_id=telegram_id
            )
            if not has_payment:
                promo_keys = config["promo_keys"]
                key = promo_keys[randint(0, len(promo_keys) - 1)]
                text_to_send = format_trial_promo_text(ts.get("ru", key))
                markup = markups.PROMO_SELECT_TARIFF_INLINE_KEYBOARD.as_markup()
            else:
                text_to_send = ts.get("ru", config["regular_key"])
                markup = markups.SELECT_TARIFF_INLINE_KEYBOARD.as_markup()

    logging.info(f"sending notification '{notification_type}' to user {telegram_id}")
    send_result = await safe_send_message(
        bot=bot, chat_id=telegram_id, text=text_to_send, markup=markup
    )

    if send_result.handled:
        logging.info(
            f"notification '{notification_type}' for user {telegram_id} handled"
        )

        if (
            send_result.sent
            and notification_type in OFFER_NOTIFICATION_TYPES
            and redis_message_broker is not None
        ):
            event_id = await ts.send_event(notification_type, telegram_id)
            if event_id is not None:
                await redis_message_broker.remember_pending_conversion(
                    telegram_id, event_id, PENDING_CONVERSION_TTL_SECONDS
                )

    return send_result.status


async def try_award_sales_referral_purchase_bonus(
    bot: Bot,
    session_maker: async_sessionmaker,
    rwms_client: RwmsClient,
    config: Config,
    telegram_id: int,
) -> None:
    async with tx(session_maker) as session:
        result = await award_sales_referral_purchase_bonus(
            session=session,
            rwms_client=rwms_client,
            config=config,
            referral_tg_id=telegram_id,
        )

    if result["status"] != "ok":
        if result["status"] not in {"wrong_referral_type", "already_awarded"}:
            logging.info(
                "sales referral purchase bonus was not applied for user %s: %s",
                telegram_id,
                result["status"],
            )
        return

    referrer = result["referrer"]
    await process_notification(
        bot=bot,
        session_maker=session_maker,
        message=ReferralPurchaseBonusApplied(
            telegram_id=referrer.telegram_id,
            referral_tariff=result["referral_tariff"],
            bonus_days_count=result["days"],
        ),
    )


def seconds_until_next_daily_report(now: datetime | None = None) -> float:
    if now is None:
        now = datetime.now(MOSCOW_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW_TZ)
    else:
        now = now.astimezone(MOSCOW_TZ)

    next_report = now.replace(
        hour=DAILY_REPORT_HOUR,
        minute=DAILY_REPORT_MINUTE,
        second=0,
        microsecond=0,
    )
    if next_report <= now:
        next_report += timedelta(days=1)

    return (next_report - now).total_seconds()


def format_notification_report(stats: dict[str, int], bot_instance_id: str) -> str:
    total = stats.get("total", 0)
    sent = stats.get("status:sent", 0)
    undeliverable = stats.get("status:undeliverable", 0)
    failed = stats.get("status:failed", 0)
    skipped = sum(
        value
        for key, value in stats.items()
        if key.startswith("status:skipped_") or key == "status:unknown_type"
    )

    lines = [
        "📬 <b>Ежедневный отчет по уведомлениям</b>",
        f"Бот: <code>{bot_instance_id}</code>",
        "Период: с прошлого отчета до 19:00 МСК",
        "",
        f"Всего обработано: <b>{total}</b>",
        f"Отправлено: <b>{sent}</b>",
        f"Недоставляемые чаты: <b>{undeliverable}</b>",
        f"Ошибки отправки: <b>{failed}</b>",
        f"Пропущено: <b>{skipped}</b>",
    ]

    type_rows = sorted(
        (
            (key.removeprefix("type:"), value)
            for key, value in stats.items()
            if key.startswith("type:")
        ),
        key=lambda item: (-item[1], item[0]),
    )

    if type_rows:
        lines.extend(["", "<b>По типам:</b>"])
        for notification_type, count in type_rows[:12]:
            lines.append(f"- <code>{notification_type}</code>: {count}")

    return "\n".join(lines)


async def send_daily_notification_report(
    bot: Bot,
    redis_message_broker: RedisMessageBroker,
    config: Config,
) -> None:
    if not config.admins:
        logging.warning("daily notification report skipped: no admins configured")
        return

    stats = await redis_message_broker.pop_notification_report_stats(
        config.bot_instance_id
    )
    report = format_notification_report(stats, config.bot_instance_id)

    for admin_id in config.admins:
        try:
            await bot.send_message(chat_id=admin_id, text=report)
        except Exception:
            logging.exception(
                "failed to send daily notification report to admin %s",
                admin_id,
            )


async def daily_notification_report_loop(
    bot: Bot,
    redis_message_broker: RedisMessageBroker,
    config: Config,
) -> None:
    while True:
        delay = seconds_until_next_daily_report()
        logging.info(
            "next daily notification report for bot_instance=%s in %.0f seconds",
            config.bot_instance_id,
            delay,
        )
        await asyncio.sleep(delay)
        await send_daily_notification_report(
            bot=bot,
            redis_message_broker=redis_message_broker,
            config=config,
        )


async def listen_notifications(
    bot: Bot,
    redis_message_broker: RedisMessageBroker,
    session_maker: async_sessionmaker,
    rwms_client: RwmsClient,
    config: Config,
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

                if not has_telegram_recipient(message):
                    logging.info(
                        "Skipping notification '%s' because no Telegram account is linked",
                        message.notification_type,
                    )
                    await redis_message_broker.increment_notification_report_stat(
                        config.bot_instance_id,
                        message.notification_type,
                        "skipped_no_recipient",
                    )
                    continue

                if message.notification_type in SUCCESS_NOTIFICATION_TYPES:
                    pending_event_id = await redis_message_broker.pop_pending_conversion(
                        message.telegram_id
                    )
                    if pending_event_id is not None:
                        await ts.convert_event(pending_event_id)

                if message.notification_type == "purchase-success-autopay":
                    logging.debug(
                        "skipping notification type 'purchase-success-autopay'"
                    )
                    await redis_message_broker.increment_notification_report_stat(
                        config.bot_instance_id,
                        message.notification_type,
                        "skipped_silent",
                    )
                    continue

                async with tx(session_maker) as session:
                    owner_bot_instance = await get_user_bot_instance(
                        session, message.telegram_id
                    )

                if owner_bot_instance is None:
                    owner_bot_instance = "primary"

                if owner_bot_instance != config.bot_instance_id:
                    logging.debug(
                        "notification for user %s belongs to bot instance %s, "
                        "current instance is %s; requeueing",
                        message.telegram_id,
                        owner_bot_instance,
                        config.bot_instance_id,
                    )
                    await redis_message_broker.requeue_message(message)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    continue

                status = await process_notification(
                    bot, session_maker, message, redis_message_broker
                )
                await redis_message_broker.increment_notification_report_stat(
                    config.bot_instance_id,
                    message.notification_type,
                    status,
                )

                if message.notification_type == "purchase-success-non-autopay":
                    await try_award_sales_referral_purchase_bonus(
                        bot=bot,
                        session_maker=session_maker,
                        rwms_client=rwms_client,
                        config=config,
                        telegram_id=message.telegram_id,
                    )
                continue

            logging.warning(f"invalid message type: {message}")
            await asyncio.sleep(random.uniform(0.5, 1))

        except Exception:
            logging.exception("notifying error")
            await asyncio.sleep(1)
