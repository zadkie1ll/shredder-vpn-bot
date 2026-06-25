import asyncio
import logging
import sqlalchemy
from collections import defaultdict
from datetime import date
from datetime import time
from datetime import datetime
from datetime import timedelta
from html import escape

from aiogram import F
from aiogram import Router
from aiogram.types import Message
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy.types import Integer
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import aliased

from handlers.broadcast_states import BroadcastStates
from utils.config import Config
from filters.is_admin import IsAdmin

from common.models.db import User
from common.models.db import EventLog
from common.models.db import ReferralBonus
from common.models.db import ReferralBonusType
from common.models.db import ReferralType
from common.models.db import UniqueReferralLink
from common.models.db import YkPayment
from common.models.db import UserTrafficProgress
from common.models.db import YkRecurrentPayment
from common.models.db import TrafficSource
from common.models.tariff import OneDayTariff
from common.models.tariff import OneMonthTariff
from common.models.tariff import ThreeMonthsTariff
from common.models.tariff import SixMonthsTariff
from common.models.tariff import OneYearTariff
from common.rwms_client import RwmsClient
from utils.public_resources import TELEGRAM_BOT_URL

from utils.rwms_helpers import update_user
import utils.referral_rewards as referral_rewards
from utils.sql_helpers import tx
from utils.sql_helpers import get_all_users
from utils.sql_helpers import extend_user_subscription_by_tg_id
from utils.sql_helpers import get_all_recurrents

service_router = Router()


@service_router.message(F.text.startswith("/extend-by-tgid"), IsAdmin())
async def __on_extend_by_tgid(
    message: Message,
    rwms_client: RwmsClient,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    # Парсим аргументы команды
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды. Используйте: /extend-by-tgid user-telegram-id number-of-days-to-add\n"
            "Пример: /extend-by-tgid 123456 90"
        )
        return

    telegram_id = None
    interval: timedelta | None = None

    try:
        telegram_id = args[0]
        interval = timedelta(days=int(args[1]))

        # Проверяем, что интервал положительный
        if interval < timedelta(days=1):
            await message.answer("❌ Интервал должен быть больше нуля")
            return

    except ValueError:
        await message.answer(
            "❌ Неверный формат user telegram id. Используйте целое число."
        )
        return

    async with tx(session_maker) as session:
        user = await rwms_client.get_user_by_username(telegram_id)

        if user is None:
            await message.answer(
                f"❌ Пользователя с username {telegram_id} не существует"
            )
            return

        await extend_user_subscription_by_tg_id(
            session=session, telegram_id=int(telegram_id), interval=interval
        )

        user_response, _ = await update_user(
            rwms_client=rwms_client, config=config, user=user, interval=interval
        )

        if user_response is not None:
            await message.answer(
                f"Подписка пользователя {telegram_id} успешно продлена на {interval.days} дней"
            )
            return


# --- ЭТАП 1: ПРЕДПРОСМОТР ---
@service_router.message(
    F.text.startswith("/sendmsg") | F.caption.startswith("/sendmsg"), IsAdmin()
)
async def __on_send_message_preview(message: Message, state: FSMContext):
    source_text = message.text or message.caption
    raw_text = source_text.replace("/sendmsg", "").strip()

    if not raw_text:
        return await message.answer("Формат: текст | кнопка | url. Можно фото.")

    parts = [p.strip() for p in raw_text.split("|")]
    msg_text = parts[0]

    # Собираем кнопки для превью
    builder = InlineKeyboardBuilder()
    if len(parts) >= 3:
        for i in range(1, len(parts), 2):
            if i + 1 < len(parts):
                builder.row(InlineKeyboardButton(text=parts[i], url=parts[i + 1]))

    photo_id = message.photo[-1].file_id if message.photo else None

    # Сохраняем данные во временное хранилище бота
    await state.update_data(
        msg_text=msg_text, photo_id=photo_id, reply_markup=builder.as_markup()
    )

    # Показываем админу превью
    await message.answer("<b>⚠️ ПРЕДПРОСМОТР СООБЩЕНИЯ:</b>", parse_mode="HTML")

    if photo_id:
        await message.answer_photo(
            photo=photo_id,
            caption=msg_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await message.answer(
            text=msg_text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    # Кнопки подтверждения
    confirm_kb = InlineKeyboardBuilder()
    confirm_kb.button(
        text="✅ Подтвердить и отправить", callback_data="broadcast_confirm"
    )
    confirm_kb.button(text="❌ Отмена", callback_data="broadcast_cancel")

    await message.answer(
        "Все верно? После нажатия кнопки сообщение уйдет всем пользователям.",
        reply_markup=confirm_kb.as_markup(),
    )
    await state.set_state(BroadcastStates.confirm)


# --- ЭТАП 2: ОБРАБОТКА ПОДТВЕРЖДЕНИЯ ---
@service_router.callback_query(BroadcastStates.confirm, F.data.startswith("broadcast_"))
async def __process_broadcast_confirm(
    call: CallbackQuery,
    state: FSMContext,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    if call.data == "broadcast_cancel":
        await state.clear()
        return await call.message.edit_text("Рассылка отменена 🫡")

    # Если подтвердили — достаем данные
    data = await state.get_data()
    msg_text = data.get("msg_text")
    photo_id = data.get("photo_id")
    reply_markup = data.get("reply_markup")

    await state.clear()
    await call.message.edit_text("🚀 Рассылка запущена...")

    async with tx(session_maker) as session:
        telegram_ids = await get_all_users(session=session)

    count = 0
    for telegram_id in telegram_ids:
        try:
            if photo_id:
                await call.bot.send_photo(
                    chat_id=telegram_id,
                    photo=photo_id,
                    caption=msg_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            else:
                await call.bot.send_message(
                    chat_id=telegram_id,
                    text=msg_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
            count += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            # Повтор (можно вынести в функцию, чтобы не дублировать)
            continue
        except Exception as e:
            logging.error(f"Error sending to {telegram_id}: {e}")

        await asyncio.sleep(0.05)  # Небольшая пауза между сообщениями

    await call.message.answer(f"✅ Рассылка завершена! Получили: {count} чел.")


def parse_report_date(value: str) -> date:
    """Парсит дату отчета в формате DD.MM.YYYY."""
    return datetime.strptime(value, "%d.%m.%Y").date()


def iter_report_dates(start_date: date, end_date: date):
    current_date = start_date
    while current_date <= end_date:
        yield current_date
        current_date += timedelta(days=1)


def source_display_name(
    traffic_source: int | None, traffic_sources: dict[int, str]
) -> str:
    if traffic_source is None:
        return "Direct"
    return traffic_sources.get(traffic_source, f"TS_{traffic_source}")


def register_first_paid_tariff(
    source_stat: dict, user_id: int, subscription_period: str
) -> None:
    if user_id in source_stat["paid_users"]:
        return

    source_stat["paid_users"].add(user_id)
    tariff_name = get_tariff_display_name(subscription_period)
    source_stat["tariff_users"][tariff_name].add(user_id)


@service_router.message(
    F.text.startswith("/trial-conversion-report")
    | F.text.startswith("/trial-report"),
    IsAdmin(),
)
async def __on_trial_conversion_report_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды.\n"
            "Используйте: "
            "<code>/trial-conversion-report 01.05.2026 31.05.2026</code>"
        )
        return

    try:
        start_date = parse_report_date(args[0])
        end_date = parse_report_date(args[1])

        if start_date > end_date:
            await message.answer("❌ Начальная дата не может быть больше конечной даты")
            return
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты. Используйте формат DD.MM.YYYY"
        )
        return

    processing_msg = await message.answer(
        "🔄 Собираем конверсию после тарифа на 3 дня..."
    )

    try:
        async with tx(session_maker) as session:
            report_data = await get_trial_conversion_report_data(
                session=session,
                start_date=start_date,
                end_date=end_date,
            )

        await processing_msg.delete()

        for report_message in generate_trial_conversion_report_messages(
            report_data=report_data,
            start_date=start_date,
            end_date=end_date,
        ):
            await message.answer(text=report_message)
            await asyncio.sleep(0.5)
    except Exception as e:
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при формировании отчета: {str(e)}")
        logging.exception("Error generating trial conversion report")


async def get_trial_conversion_report_data(
    session,
    start_date: date,
    end_date: date,
) -> dict:
    """Finds the first paid tariff after a three-day purchase in the period."""
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    trial_payments = (
        select(
            YkPayment.user_id.label("user_id"),
            func.min(YkPayment.created_at).label("trial_purchased_at"),
        )
        .where(
            and_(
                YkPayment.status == "succeeded",
                YkPayment.subscription_period == "threedays",
                YkPayment.created_at >= start_datetime,
                YkPayment.created_at <= end_datetime,
            )
        )
        .group_by(YkPayment.user_id)
        .subquery()
    )

    subsequent_payment = aliased(YkPayment)
    payments_result = await session.execute(
        select(
            trial_payments.c.user_id,
            trial_payments.c.trial_purchased_at,
            User.telegram_id,
            User.telegram_username,
            subsequent_payment.subscription_period,
            subsequent_payment.created_at,
            subsequent_payment.amount,
        )
        .join(User, User.id == trial_payments.c.user_id)
        .outerjoin(
            subsequent_payment,
            and_(
                subsequent_payment.user_id == trial_payments.c.user_id,
                subsequent_payment.status == "succeeded",
                subsequent_payment.subscription_period != "threedays",
                subsequent_payment.created_at
                > trial_payments.c.trial_purchased_at,
            ),
        )
        .order_by(
            trial_payments.c.trial_purchased_at.asc(),
            subsequent_payment.created_at.asc(),
        )
    )

    trial_users = {}
    for row in payments_result.all():
        user = trial_users.setdefault(
            row.user_id,
            {
                "telegram_id": row.telegram_id,
                "telegram_username": row.telegram_username,
                "trial_purchased_at": row.trial_purchased_at,
                "next_payment_at": None,
                "next_tariff": None,
                "next_payment_amount": None,
            },
        )

        if row.created_at is not None and user["next_payment_at"] is None:
            user["next_payment_at"] = row.created_at
            user["next_tariff"] = row.subscription_period
            user["next_payment_amount"] = row.amount

    converted_users = [
        user for user in trial_users.values() if user["next_payment_at"] is not None
    ]
    converted_users.sort(key=lambda user: user["next_payment_at"])

    tariff_stats = defaultdict(int)
    for user in converted_users:
        tariff_stats[get_tariff_display_name(user["next_tariff"])] += 1

    return {
        "trial_users_count": len(trial_users),
        "converted_users": converted_users,
        "tariff_stats": dict(tariff_stats),
    }


def generate_trial_conversion_report_messages(
    report_data: dict,
    start_date: date,
    end_date: date,
) -> list[str]:
    trial_users_count = report_data["trial_users_count"]
    converted_users = report_data["converted_users"]
    converted_count = len(converted_users)
    conversion_rate = (
        converted_count / trial_users_count * 100 if trial_users_count else 0
    )

    summary_lines = [
        (
            "🔁 <b>КОНВЕРСИЯ ПОСЛЕ ТАРИФА НА 3 ДНЯ</b>\n"
            f"Период покупки тарифа: "
            f"<b>{start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}</b>"
        ),
        "",
        f"Купили тариф на 3 дня: <b>{trial_users_count}</b>",
        f"Купили другой тариф позже: <b>{converted_count}</b>",
        f"Конверсия: <b>{conversion_rate:.1f}%</b>",
    ]

    tariff_stats = report_data.get("tariff_stats", {})
    if tariff_stats:
        summary_lines.extend(["", "<b>Первый следующий тариф:</b>"])
        for tariff_name, count in sorted(
            tariff_stats.items(),
            key=lambda item: get_tariff_order(item[0]),
        ):
            summary_lines.append(f"- {escape(tariff_name)}: <b>{count}</b>")

    if not converted_users:
        summary_lines.extend(["", "<i>Конверсий за выбранный период нет.</i>"])
        return ["\n".join(summary_lines)]

    detail_lines = ["👥 <b>СКОНВЕРТИРОВАВШИЕСЯ ПОЛЬЗОВАТЕЛИ</b>", ""]
    for index, user in enumerate(converted_users, 1):
        telegram_username = user.get("telegram_username")
        username = (
            f"@{escape(telegram_username)}" if telegram_username else "@unknown"
        )
        detail_lines.append(
            f"{index}. <code>{user['telegram_id']}</code> ({username})\n"
            f"   3 дня: {user['trial_purchased_at']:%d.%m.%Y %H:%M}\n"
            f"   затем: {escape(get_tariff_display_name(user['next_tariff']))}, "
            f"{user['next_payment_amount']} ₽, "
            f"{user['next_payment_at']:%d.%m.%Y %H:%M}"
        )

    return ["\n".join(summary_lines)] + split_message("\n".join(detail_lines))


@service_router.message(
    F.text.startswith("/table-report") | F.text.startswith("/sheet-report"), IsAdmin()
)
async def __on_table_report_requested(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды.\n"
            "Используйте: <code>/table-report 01.05.2026 07.05.2026</code>"
        )
        return

    try:
        start_date = parse_report_date(args[0])
        end_date = parse_report_date(args[1])

        if start_date > end_date:
            await message.answer("❌ Начальная дата не может быть больше конечной даты")
            return
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты. Используйте формат DD.MM.YYYY"
        )
        return

    processing_msg = await message.answer("🔄 Собираем отчет для таблицы...")

    try:
        async with tx(session_maker) as session:
            report_data = await get_table_report_data(
                session=session,
                start_date=start_date,
                end_date=end_date,
                trial_period_days=config.trial_period_days,
            )

        report_messages = generate_table_report_messages(
            report_data=report_data,
            start_date=start_date,
            end_date=end_date,
            trial_period_days=config.trial_period_days,
        )

        await processing_msg.delete()

        for report_message in report_messages:
            await message.answer(text=report_message)
            await asyncio.sleep(0.5)
    except Exception as e:
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при формировании отчета: {str(e)}")
        logging.exception("Error generating table report")


async def get_table_report_data(
    session,
    start_date: date,
    end_date: date,
    trial_period_days: int,
) -> dict:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    trial_lookup_start = start_datetime - timedelta(days=trial_period_days)

    subscription_events_query = (
        select(EventLog.user_id, EventLog.event_payload, EventLog.timestamp)
        .where(
            and_(
                EventLog.event_type == "subscription_created",
                EventLog.timestamp >= trial_lookup_start,
                EventLog.timestamp <= end_datetime,
            )
        )
        .order_by(EventLog.timestamp.asc())
    )
    subscription_events_result = await session.execute(subscription_events_query)
    subscription_events = subscription_events_result.all()

    report_user_ids = {
        row.user_id
        for row in subscription_events
        if start_datetime <= row.timestamp <= end_datetime
    }
    trial_window_user_ids = {row.user_id for row in subscription_events}
    tracked_user_ids = report_user_ids | trial_window_user_ids

    payment_filters = [
        and_(
            YkPayment.created_at >= start_datetime,
            YkPayment.created_at <= end_datetime,
        )
    ]

    if tracked_user_ids:
        payment_filters.append(YkPayment.user_id.in_(tracked_user_ids))

    payments_query = (
        select(
            YkPayment.user_id,
            YkPayment.subscription_period,
        )
        .where(
            and_(
                YkPayment.status == "succeeded",
                or_(*payment_filters),
            )
        )
        .order_by(YkPayment.created_at.asc())
    )
    payments_result = await session.execute(payments_query)
    payments = payments_result.all()

    traffic_sources_result = await session.execute(select(TrafficSource))
    traffic_sources = {
        traffic_source.id: traffic_source.name
        for traffic_source in traffic_sources_result.scalars().all()
    }

    users = {}
    source_stats = defaultdict(
        lambda: {
            "new_users": 0,
            "paid_users": set(),
            "tariff_users": defaultdict(set),
        }
    )

    for row in subscription_events:
        traffic_source = row.event_payload.get("traffic_source")
        users[row.user_id] = {
            "created_at": row.timestamp,
            "traffic_source": traffic_source,
        }

        created_date = row.timestamp.date()
        if start_date <= created_date <= end_date:
            source_stats[traffic_source]["new_users"] += 1

    for payment in payments:
        user_info = users.get(payment.user_id)
        if user_info is not None and payment.user_id in report_user_ids:
            register_first_paid_tariff(
                source_stat=source_stats[user_info["traffic_source"]],
                user_id=payment.user_id,
                subscription_period=payment.subscription_period,
            )

    source_rows = []
    for traffic_source, stats in source_stats.items():
        tariff_stats = {
            tariff_name: len(user_ids)
            for tariff_name, user_ids in stats["tariff_users"].items()
        }
        source_rows.append(
            {
                "source": source_display_name(traffic_source, traffic_sources),
                "new_users": stats["new_users"],
                "paid_users": len(stats["paid_users"]),
                "tariff_stats": tariff_stats,
            }
        )

    source_rows.sort(key=lambda row: row["new_users"], reverse=True)

    daily_stats = await get_daily_table_report_stats_by_interval(
        session=session,
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "daily_stats": daily_stats,
        "source_rows": source_rows,
    }


async def get_daily_table_report_stats_by_interval(
    session,
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict:
    """Gets the daily stats block used as the first part of table-report."""
    from sqlalchemy import exists

    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    daily_stats = {
        current_date: {
            "entered_bot_user_ids": set(),
            "connected_user_ids": set(),
            "paid_user_ids": set(),
            "payments_count": 0,
            "payments_sum": 0,
            "tariff_stats": defaultdict(int),
            "not_renewed_user_ids": set(),
        }
        for current_date in iter_report_dates(start_date, end_date)
    }

    created_events_query = (
        select(EventLog.user_id, EventLog.timestamp)
        .where(
            and_(
                EventLog.event_type == "subscription_created",
                EventLog.timestamp >= start_datetime,
                EventLog.timestamp <= end_datetime,
            )
        )
    )

    created_events_result = await session.execute(created_events_query)
    for user_id, timestamp in created_events_result.all():
        daily_stats[timestamp.date()]["entered_bot_user_ids"].add(user_id)

    connected_events_query = (
        select(EventLog.user_id, EventLog.timestamp)
        .where(
            and_(
                EventLog.event_type == "traffic_threshold_reached",
                EventLog.timestamp >= start_datetime,
                EventLog.timestamp <= end_datetime,
                EventLog.event_payload["threshold"].astext.cast(Integer) == 0,
            )
        )
    )

    connected_events_result = await session.execute(connected_events_query)
    for user_id, timestamp in connected_events_result.all():
        daily_stats[timestamp.date()]["connected_user_ids"].add(user_id)

    payments_query = (
        select(
            YkPayment.user_id,
            YkPayment.amount,
            YkPayment.subscription_period,
            YkPayment.created_at,
        )
        .where(
            and_(
                YkPayment.status == "succeeded",
                YkPayment.created_at >= start_datetime,
                YkPayment.created_at <= end_datetime,
            )
        )
    )

    payments_result = await session.execute(payments_query)
    for user_id, amount, subscription_period, created_at in payments_result.all():
        day_stats = daily_stats[created_at.date()]
        day_stats["paid_user_ids"].add(user_id)
        day_stats["payments_count"] += 1
        day_stats["payments_sum"] += amount or 0
        day_stats["tariff_stats"][get_tariff_display_name(subscription_period)] += 1

    payment_after_expiration = aliased(YkPayment)
    not_renewed_query = (
        select(User.id, User.expire_at)
        .where(
            and_(
                User.expire_at.isnot(None),
                User.expire_at >= start_datetime,
                User.expire_at <= end_datetime,
                ~exists().where(
                    and_(
                        payment_after_expiration.user_id == User.id,
                        payment_after_expiration.status == "succeeded",
                        payment_after_expiration.created_at > User.expire_at,
                    )
                ),
            )
        )
    )

    not_renewed_result = await session.execute(not_renewed_query)
    for user_id, expire_at in not_renewed_result.all():
        daily_stats[expire_at.date()]["not_renewed_user_ids"].add(user_id)

    return daily_stats


def generate_table_report_messages(
    report_data: dict,
    start_date: date,
    end_date: date,
    trial_period_days: int,
) -> list[str]:
    daily_stats = report_data["daily_stats"]
    source_rows = report_data["source_rows"]

    source_lines = [
        f"📣 <b>ИСТОЧНИКИ ЗА ПЕРИОД {start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}</b>",
        "",
        "<code>Источник | Прибавилось | Перешли на платный",
    ]

    if source_rows:
        for source_row in source_rows:
            source_lines.append(
                f"{escape(source_row['source'])} | "
                f"{source_row['new_users']} | "
                f"{source_row['paid_users']}"
            )
            tariff_stats = source_row.get("tariff_stats", {})
            if tariff_stats:
                tariff_parts = [
                    f"{escape(tariff_name)}: {count}"
                    for tariff_name, count in sorted(
                        tariff_stats.items(),
                        key=lambda item: get_tariff_order(item[0]),
                    )
                ]
                source_lines.append(f"  Тариф перехода: {' | '.join(tariff_parts)}")
    else:
        source_lines.append("нет данных | 0 | 0")

    source_lines[-1] = f"{source_lines[-1]}</code>"

    return split_message(
        generate_daily_table_report(daily_stats, start_date, end_date)
    ) + ["\n".join(source_lines)]


def format_admin_user(user: User | None) -> str:
    if user is None:
        return "unknown"

    telegram_username = getattr(user, "telegram_username", None)
    username = f"@{escape(telegram_username)}" if telegram_username else "@unknown"
    return f"<code>{user.telegram_id}</code> ({username})"


def get_referral_type_display_name(referral_type: ReferralType | str | None) -> str:
    names = {
        ReferralType.STANDARD.value: "Стандартная",
        ReferralType.ONLY_REGISTRATIONS.value: "Только регистрации",
        ReferralType.ALL_PAYMENTS_PERCENTAGE.value: "Процент со всех оплат",
        ReferralType.SALES_PURCHASE.value: "Бонус по купленному тарифу",
    }
    value = (
        referral_type.value
        if isinstance(referral_type, ReferralType)
        else referral_type
    )
    return names.get(value, "Не указан")


def get_referral_bonus_type_display_name(
    bonus_type: ReferralBonusType | str,
) -> str:
    names = {
        ReferralBonusType.REGISTRATION.value: "За регистрацию",
        ReferralBonusType.TRAFFIC.value: "За трафик",
        ReferralBonusType.PURCHASE.value: "За покупку",
    }
    value = (
        bonus_type.value if isinstance(bonus_type, ReferralBonusType) else bonus_type
    )
    return names.get(value, str(value))


@service_router.message(F.text.startswith("/ref-report"), IsAdmin())
async def __on_referral_report_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды.\n"
            "Используйте: <code>/ref-report 01.05.2026 07.05.2026</code>"
        )
        return

    try:
        start_date = parse_report_date(args[0])
        end_date = parse_report_date(args[1])

        if start_date > end_date:
            await message.answer("❌ Начальная дата не может быть больше конечной даты")
            return
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты. Используйте формат DD.MM.YYYY"
        )
        return

    processing_msg = await message.answer("🔄 Собираем отчет по рефералке...")

    try:
        async with tx(session_maker) as session:
            report_data = await get_referral_report_data(
                session=session,
                start_date=start_date,
                end_date=end_date,
            )

        await processing_msg.delete()

        for report_message in generate_referral_report_messages(
            report_data=report_data,
            start_date=start_date,
            end_date=end_date,
        ):
            await message.answer(text=report_message)
            await asyncio.sleep(0.5)
    except Exception as e:
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при формировании отчета: {str(e)}")
        logging.exception("Error generating referral report")


async def get_referral_report_data(session, start_date: date, end_date: date) -> dict:
    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)
    referral_user = aliased(User)
    referrer_user = aliased(User)

    referrals_query = (
        select(referral_user, referrer_user, EventLog.timestamp)
        .join(EventLog, EventLog.user_id == referral_user.id)
        .join(referrer_user, referral_user.referred_by_id == referrer_user.id)
        .where(
            and_(
                EventLog.event_type == "subscription_created",
                EventLog.timestamp >= start_datetime,
                EventLog.timestamp <= end_datetime,
                referral_user.referred_by_id.is_not(None),
            )
        )
        .order_by(EventLog.timestamp.asc())
    )
    referrals_result = await session.execute(referrals_query)
    referrals = referrals_result.all()

    referral_ids = {referral.id for referral, _, _ in referrals}
    referrer_ids = {referrer.id for _, referrer, _ in referrals}

    paid_referral_ids = set()
    if referral_ids:
        payments_result = await session.execute(
            select(YkPayment.user_id)
            .where(
                and_(
                    YkPayment.status == "succeeded",
                    YkPayment.user_id.in_(referral_ids),
                )
            )
            .distinct()
        )
        paid_referral_ids = set(payments_result.scalars().all())

    bonus_days_by_referrer = defaultdict(int)
    if referrer_ids and referral_ids:
        bonuses_result = await session.execute(
            select(
                ReferralBonus.referrer_id,
                func.coalesce(func.sum(ReferralBonus.days_added), 0),
            )
            .where(
                and_(
                    ReferralBonus.referrer_id.in_(referrer_ids),
                    ReferralBonus.referral_id.in_(referral_ids),
                )
            )
            .group_by(ReferralBonus.referrer_id)
        )
        for referrer_id, bonus_days in bonuses_result.all():
            bonus_days_by_referrer[referrer_id] = bonus_days or 0

    referrers = {}
    seen_referral_ids = set()
    for referral, referrer, created_at in referrals:
        if referral.id in seen_referral_ids:
            continue
        seen_referral_ids.add(referral.id)

        if referrer.id not in referrers:
            referrers[referrer.id] = {
                "user": referrer,
                "invited": 0,
                "paid": 0,
                "bonus_days": 0,
                "last_invite_at": created_at,
            }

        referrer_stats = referrers[referrer.id]
        referrer_stats["invited"] += 1
        referrer_stats["bonus_days"] = bonus_days_by_referrer[referrer.id]
        referrer_stats["last_invite_at"] = max(
            referrer_stats["last_invite_at"],
            created_at,
        )

        if referral.id in paid_referral_ids:
            referrer_stats["paid"] += 1

    referrer_rows = sorted(
        referrers.values(),
        key=lambda row: (row["invited"], row["paid"]),
        reverse=True,
    )

    return {
        "invited_count": len(referral_ids),
        "paid_count": len(paid_referral_ids),
        "referrer_rows": referrer_rows,
    }


def generate_referral_report_messages(
    report_data: dict,
    start_date: date,
    end_date: date,
) -> list[str]:
    invited_count = report_data["invited_count"]
    paid_count = report_data["paid_count"]
    referrer_rows = report_data["referrer_rows"]
    conversion = (paid_count / invited_count * 100) if invited_count else 0

    lines = [
        f"🤝 <b>РЕФЕРАЛКА ЗА ПЕРИОД {start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}</b>",
        "",
        f"Всего приглашено: <b>{invited_count}</b>",
        f"Перешли на платный: <b>{paid_count}</b>",
        f"Конверсия в оплату: <b>{conversion:.1f}%</b>",
        "",
        "<b>Топ приглашающих:</b>",
    ]

    if not referrer_rows:
        lines.append("<i>За период приглашений не найдено</i>")
        return ["\n".join(lines)]

    for index, row in enumerate(referrer_rows[:20], 1):
        user = row["user"]
        lines.append(
            f"{index}. {format_admin_user(user)} | "
            f"пригласил {row['invited']} | "
            f"оплатили {row['paid']} | "
            f"бонус {row['bonus_days']} дн."
        )

    if len(referrer_rows) > 20:
        lines.append(f"\n<i>Показаны первые 20 из {len(referrer_rows)}.</i>")

    return split_message("\n".join(lines))


@service_router.message(
    F.text.startswith("/refs") | F.text.startswith("/ref-stats"), IsAdmin()
)
async def __on_user_referrals_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if not args:
        await message.answer(
            "❌ Введите Telegram ID пользователя.\n"
            "Пример: <code>/ref-stats 123456789</code>"
        )
        return

    try:
        target_tg_id = int(args[0])
    except ValueError:
        await message.answer("❌ Telegram ID должен быть числом.")
        return

    processing_msg = await message.answer("🔄 Собираем реферальную статистику...")

    try:
        async with tx(session_maker) as session:
            report_data = await get_user_referrals_data(
                session=session,
                target_tg_id=target_tg_id,
            )

        await processing_msg.delete()

        for report_message in generate_user_referrals_messages(report_data):
            await message.answer(text=report_message)
            await asyncio.sleep(0.5)
    except Exception as e:
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при формировании отчета: {str(e)}")
        logging.exception("Error generating user referrals report")


@service_router.message(
    F.text.startswith("/uniq_ref_gen") | F.text.startswith("/sales-ref"), IsAdmin()
)
async def __on_sales_referral_link_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if not args:
        await message.answer(
            "❌ Введите Telegram ID пользователя.\n"
            "Пример: <code>/uniq_ref_gen 123456789</code>"
        )
        return

    try:
        target_tg_id = int(args[0])
    except ValueError:
        await message.answer("❌ Telegram ID должен быть числом.")
        return

    async with tx(session_maker) as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == target_tg_id).limit(1)
        )

        if user is not None:
            await session.execute(
                text("""
                    INSERT INTO unique_referral_links (
                        user_id, created_by_telegram_id
                    ) VALUES (
                        :user_id, :created_by_telegram_id
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        created_by_telegram_id = EXCLUDED.created_by_telegram_id
                """),
                {
                    "user_id": user.id,
                    "created_by_telegram_id": message.from_user.id,
                },
            )

    if user is None:
        await message.answer("❌ Пользователь с таким Telegram ID не найден.")
        return

    referral_username = user.username or str(user.telegram_id)
    referral_url = f"{TELEGRAM_BOT_URL}?start=s{referral_username}"

    await message.answer(
        "✅ Уникальная реферальная ссылка создана.\n\n"
        f"Пользователь: {format_admin_user(user)}\n"
        "Условие: пригласивший получит столько дней, сколько купит реферал.\n"
        "Реферал не получает увеличенный пробный период по этой ссылке.\n\n"
        f"<code>{referral_url}</code>"
    )


@service_router.message(F.text.startswith("/uniq_ref_stats"), IsAdmin())
async def __on_unique_referral_stats_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    limit = 30

    if args:
        try:
            limit = int(args[0])
            if limit < 1 or limit > 100:
                raise ValueError
        except ValueError:
            await message.answer(
                "❌ Лимит должен быть числом от 1 до 100.\n"
                "Пример: <code>/uniq_ref_stats 50</code>"
            )
            return

    async with tx(session_maker) as session:
        rows = await get_unique_referral_stats_rows(session=session, limit=limit)

    for report_message in generate_unique_referral_stats_messages(rows):
        await message.answer(text=report_message)
        await asyncio.sleep(0.5)


async def get_unique_referral_stats_rows(session, limit: int) -> list[dict]:
    referral_user = aliased(User)
    referrer_user = aliased(User)

    referrals_subquery = (
        select(
            referral_user.referred_by_id.label("referrer_id"),
            func.count(referral_user.id).label("referrals_count"),
        )
        .where(
            and_(
                referral_user.referral_type == ReferralType.SALES_PURCHASE,
                referral_user.referred_by_id.is_not(None),
            )
        )
        .group_by(referral_user.referred_by_id)
        .subquery()
    )

    payments_subquery = (
        select(
            referral_user.referred_by_id.label("referrer_id"),
            func.count(func.distinct(referral_user.id)).label("paid_referrals_count"),
            func.count(YkPayment.id).label("payments_count"),
            func.coalesce(func.sum(YkPayment.amount), 0).label("payments_sum"),
        )
        .join(YkPayment, YkPayment.user_id == referral_user.id)
        .where(
            and_(
                referral_user.referral_type == ReferralType.SALES_PURCHASE,
                YkPayment.status == "succeeded",
            )
        )
        .group_by(referral_user.referred_by_id)
        .subquery()
    )

    bonuses_subquery = (
        select(
            ReferralBonus.referrer_id.label("referrer_id"),
            func.coalesce(func.sum(ReferralBonus.days_added), 0).label("bonus_days"),
        )
        .join(referral_user, referral_user.id == ReferralBonus.referral_id)
        .where(
            and_(
                referral_user.referral_type == ReferralType.SALES_PURCHASE,
                ReferralBonus.bonus_type == ReferralBonusType.PURCHASE,
            )
        )
        .group_by(ReferralBonus.referrer_id)
        .subquery()
    )

    result = await session.execute(
        select(
            UniqueReferralLink,
            referrer_user,
            func.coalesce(referrals_subquery.c.referrals_count, 0),
            func.coalesce(payments_subquery.c.paid_referrals_count, 0),
            func.coalesce(payments_subquery.c.payments_count, 0),
            func.coalesce(payments_subquery.c.payments_sum, 0),
            func.coalesce(bonuses_subquery.c.bonus_days, 0),
        )
        .join(referrer_user, referrer_user.id == UniqueReferralLink.user_id)
        .outerjoin(
            referrals_subquery, referrals_subquery.c.referrer_id == referrer_user.id
        )
        .outerjoin(
            payments_subquery, payments_subquery.c.referrer_id == referrer_user.id
        )
        .outerjoin(bonuses_subquery, bonuses_subquery.c.referrer_id == referrer_user.id)
        .order_by(
            func.coalesce(payments_subquery.c.paid_referrals_count, 0).desc(),
            func.coalesce(referrals_subquery.c.referrals_count, 0).desc(),
        )
        .limit(limit)
    )

    return [
        {
            "link": link,
            "referrer": referrer,
            "referrals_count": referrals_count or 0,
            "paid_referrals_count": paid_referrals_count or 0,
            "payments_count": payments_count or 0,
            "payments_sum": payments_sum or 0,
            "bonus_days": bonus_days or 0,
        }
        for (
            link,
            referrer,
            referrals_count,
            paid_referrals_count,
            payments_count,
            payments_sum,
            bonus_days,
        ) in result.all()
    ]


def generate_unique_referral_stats_messages(rows: list[dict]) -> list[str]:
    lines = [
        "🔗 <b>УНИКАЛЬНЫЕ РЕФЕРАЛКИ</b>",
        "",
        "Показаны пользователи, для которых выпускали уникальные ссылки.",
        "Деталка: <code>/uniq_ref_detail TGID</code>",
        "",
    ]

    if not rows:
        lines.append("<i>Уникальные реферальные ссылки пока не выпускались.</i>")
        return ["\n".join(lines)]

    for index, row in enumerate(rows, 1):
        referrer = row["referrer"]
        conversion = (
            row["paid_referrals_count"] / row["referrals_count"] * 100
            if row["referrals_count"]
            else 0
        )
        lines.append(
            f"{index}. {format_admin_user(referrer)} | "
            f"привел {row['referrals_count']} | "
            f"оплатили {row['paid_referrals_count']} ({conversion:.1f}%) | "
            f"{row['payments_sum']} ₽ | "
            f"бонус {row['bonus_days']} дн."
        )

    return split_message("\n".join(lines))


@service_router.message(F.text.startswith("/uniq_ref_detail"), IsAdmin())
async def __on_unique_referral_detail_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if not args:
        await message.answer(
            "❌ Введите Telegram ID пользователя.\n"
            "Пример: <code>/uniq_ref_detail 123456789</code>"
        )
        return

    try:
        target_tg_id = int(args[0])
    except ValueError:
        await message.answer("❌ Telegram ID должен быть числом.")
        return

    async with tx(session_maker) as session:
        report_data = await get_unique_referral_detail_data(
            session=session,
            target_tg_id=target_tg_id,
        )

    for report_message in generate_unique_referral_detail_messages(report_data):
        await message.answer(text=report_message)
        await asyncio.sleep(0.5)


async def get_unique_referral_detail_data(session, target_tg_id: int) -> dict:
    referrer = await session.scalar(
        select(User).where(User.telegram_id == target_tg_id).limit(1)
    )

    if referrer is None:
        return {
            "referrer": None,
            "referrals": [],
            "paid_count": 0,
            "payments_sum": 0,
            "bonus_days": 0,
            "link": None,
        }

    link = await session.scalar(
        select(UniqueReferralLink).where(UniqueReferralLink.user_id == referrer.id)
    )

    referrals_result = await session.execute(
        select(User)
        .where(
            and_(
                User.referred_by_id == referrer.id,
                User.referral_type == ReferralType.SALES_PURCHASE,
            )
        )
        .order_by(User.id.asc())
    )
    referrals = referrals_result.scalars().all()
    referral_ids = {referral.id for referral in referrals}

    first_seen_by_user = {}
    payments_by_user = defaultdict(list)
    bonus_days_by_referral = defaultdict(int)

    if referral_ids:
        first_seen_result = await session.execute(
            select(EventLog.user_id, func.min(EventLog.timestamp))
            .where(
                and_(
                    EventLog.event_type == "subscription_created",
                    EventLog.user_id.in_(referral_ids),
                )
            )
            .group_by(EventLog.user_id)
        )
        first_seen_by_user = dict(first_seen_result.all())

        payments_result = await session.execute(
            select(YkPayment)
            .where(
                and_(
                    YkPayment.status == "succeeded",
                    YkPayment.user_id.in_(referral_ids),
                )
            )
            .order_by(YkPayment.created_at.asc())
        )
        for payment in payments_result.scalars().all():
            payments_by_user[payment.user_id].append(payment)

        bonuses_result = await session.execute(
            select(
                ReferralBonus.referral_id,
                func.coalesce(func.sum(ReferralBonus.days_added), 0),
            )
            .where(
                and_(
                    ReferralBonus.referrer_id == referrer.id,
                    ReferralBonus.referral_id.in_(referral_ids),
                    ReferralBonus.bonus_type == ReferralBonusType.PURCHASE,
                )
            )
            .group_by(ReferralBonus.referral_id)
        )
        for referral_id, bonus_days in bonuses_result.all():
            bonus_days_by_referral[referral_id] = bonus_days or 0

    referral_rows = []
    for referral in referrals:
        payments = payments_by_user[referral.id]
        payments_sum = sum(payment.amount for payment in payments)
        first_payment = payments[0] if payments else None
        referral_rows.append(
            {
                "user": referral,
                "first_seen": first_seen_by_user.get(referral.id),
                "payments_count": len(payments),
                "payments_sum": payments_sum,
                "first_payment": first_payment,
                "bonus_days": bonus_days_by_referral[referral.id],
            }
        )

    referral_rows.sort(
        key=lambda row: row["first_seen"] or datetime.min,
        reverse=True,
    )

    return {
        "referrer": referrer,
        "referrals": referral_rows,
        "paid_count": sum(1 for row in referral_rows if row["payments_count"] > 0),
        "payments_sum": sum(row["payments_sum"] for row in referral_rows),
        "bonus_days": sum(row["bonus_days"] for row in referral_rows),
        "link": link,
    }


def generate_unique_referral_detail_messages(report_data: dict) -> list[str]:
    referrer = report_data["referrer"]

    if referrer is None:
        return ["❌ Пользователь с таким Telegram ID не найден."]

    referral_rows = report_data["referrals"]
    link = report_data["link"]
    paid_count = report_data["paid_count"]
    conversion = (paid_count / len(referral_rows) * 100) if referral_rows else 0
    link_text = (
        link.created_at.strftime("%d.%m.%Y %H:%M")
        if link is not None
        else "не выпускалась"
    )

    lines = [
        "🔗 <b>ДЕТАЛИ УНИКАЛЬНОЙ РЕФЕРАЛКИ</b>",
        f"Кто приглашал: {format_admin_user(referrer)}",
        f"Ссылка выпущена: <b>{link_text}</b>",
        "",
        f"Всего приведено: <b>{len(referral_rows)}</b>",
        f"Оплатили: <b>{paid_count}</b> ({conversion:.1f}%)",
        f"Сумма оплат: <b>{report_data['payments_sum']} ₽</b>",
        f"Начислено бонусов: <b>{report_data['bonus_days']} дн.</b>",
        "",
    ]

    if not referral_rows:
        lines.append(
            "<i>По уникальной ссылке этого пользователя переходов пока нет.</i>"
        )
        return ["\n".join(lines)]

    lines.append("<b>Рефералы:</b>")

    for index, row in enumerate(referral_rows[:40], 1):
        referral = row["user"]
        first_seen = row["first_seen"]
        first_seen_text = (
            first_seen.strftime("%d.%m.%Y") if first_seen else "нет даты"
        )

        if row["first_payment"] is None:
            payment_text = "нет оплат"
        else:
            first_payment = row["first_payment"]
            payment_text = (
                f"{row['payments_count']} оплат / {row['payments_sum']} ₽ / "
                f"первая: {get_tariff_display_name(first_payment.subscription_period)} "
                f"{first_payment.created_at:%d.%m.%Y}"
            )

        lines.append(
            f"{index}. {format_admin_user(referral)} | "
            f"старт {first_seen_text} | "
            f"{payment_text} | "
            f"бонус {row['bonus_days']} дн."
        )

    if len(referral_rows) > 40:
        lines.append(f"\n<i>Показаны первые 40 из {len(referral_rows)}.</i>")

    return split_message("\n".join(lines))


async def get_user_referrals_data(session, target_tg_id: int) -> dict:
    referrer = await session.scalar(
        select(User).where(User.telegram_id == target_tg_id)
    )

    if referrer is None:
        return {
            "referrer": None,
            "invited_by": None,
            "referrals": [],
            "paid_count": 0,
            "payments_count": 0,
            "payments_sum": 0,
            "bonus_days": 0,
            "bonus_stats": {},
            "referral_type_stats": {},
            "tariff_stats": {},
            "link": None,
        }

    invited_by = None
    if referrer.referred_by_id is not None:
        invited_by = await session.scalar(
            select(User).where(User.id == referrer.referred_by_id)
        )

    link = await session.scalar(
        select(UniqueReferralLink).where(UniqueReferralLink.user_id == referrer.id)
    )

    referrals_result = await session.execute(
        select(User)
        .where(User.referred_by_id == referrer.id)
        .order_by(User.id.asc())
    )
    referrals = referrals_result.scalars().all()
    referral_ids = {referral.id for referral in referrals}

    first_seen_by_user = {}
    paid_by_user = defaultdict(lambda: {"count": 0, "sum": 0})
    bonus_days_by_referral = defaultdict(int)
    bonus_stats = defaultdict(int)
    tariff_stats = defaultdict(lambda: {"payments_count": 0, "payments_sum": 0})

    if referral_ids:
        first_seen_result = await session.execute(
            select(EventLog.user_id, func.min(EventLog.timestamp))
            .where(
                and_(
                    EventLog.event_type == "subscription_created",
                    EventLog.user_id.in_(referral_ids),
                )
            )
            .group_by(EventLog.user_id)
        )
        first_seen_by_user = dict(first_seen_result.all())

        payments_result = await session.execute(
            select(
                YkPayment.user_id,
                func.count(YkPayment.id),
                func.coalesce(func.sum(YkPayment.amount), 0),
            )
            .where(
                and_(
                    YkPayment.status == "succeeded",
                    YkPayment.user_id.in_(referral_ids),
                )
            )
            .group_by(YkPayment.user_id)
        )
        for user_id, payment_count, payment_sum in payments_result.all():
            paid_by_user[user_id] = {
                "count": payment_count or 0,
                "sum": payment_sum or 0,
            }

        tariff_stats_result = await session.execute(
            select(
                YkPayment.subscription_period,
                func.count(YkPayment.id),
                func.coalesce(func.sum(YkPayment.amount), 0),
            )
            .where(
                and_(
                    YkPayment.status == "succeeded",
                    YkPayment.user_id.in_(referral_ids),
                )
            )
            .group_by(YkPayment.subscription_period)
        )
        for (
            subscription_period,
            payment_count,
            payment_sum,
        ) in tariff_stats_result.all():
            tariff_stats[get_tariff_display_name(subscription_period)] = {
                "payments_count": payment_count or 0,
                "payments_sum": payment_sum or 0,
            }

        bonuses_result = await session.execute(
            select(
                ReferralBonus.referral_id,
                ReferralBonus.bonus_type,
                func.coalesce(func.sum(ReferralBonus.days_added), 0),
            )
            .where(
                and_(
                    ReferralBonus.referrer_id == referrer.id,
                    ReferralBonus.referral_id.in_(referral_ids),
                )
            )
            .group_by(ReferralBonus.referral_id, ReferralBonus.bonus_type)
        )
        for referral_id, bonus_type, bonus_days in bonuses_result.all():
            days = bonus_days or 0
            bonus_days_by_referral[referral_id] += days
            bonus_stats[get_referral_bonus_type_display_name(bonus_type)] += days

    referral_rows = []
    referral_type_stats = defaultdict(int)
    for referral in referrals:
        payments = paid_by_user[referral.id]
        referral_type_name = get_referral_type_display_name(referral.referral_type)
        referral_type_stats[referral_type_name] += 1
        referral_rows.append(
            {
                "user": referral,
                "first_seen": first_seen_by_user.get(referral.id),
                "payments_count": payments["count"],
                "payments_sum": payments["sum"],
                "bonus_days": bonus_days_by_referral[referral.id],
                "referral_type": referral_type_name,
            }
        )

    referral_rows.sort(
        key=lambda row: row["first_seen"] or datetime.min,
        reverse=True,
    )

    return {
        "referrer": referrer,
        "invited_by": invited_by,
        "referrals": referral_rows,
        "paid_count": sum(1 for row in referral_rows if row["payments_count"] > 0),
        "payments_count": sum(row["payments_count"] for row in referral_rows),
        "payments_sum": sum(row["payments_sum"] for row in referral_rows),
        "bonus_days": sum(row["bonus_days"] for row in referral_rows),
        "bonus_stats": dict(bonus_stats),
        "referral_type_stats": dict(referral_type_stats),
        "tariff_stats": dict(tariff_stats),
        "link": link,
    }


def generate_user_referrals_messages(report_data: dict) -> list[str]:
    referrer = report_data["referrer"]

    if referrer is None:
        return ["❌ Пользователь с таким Telegram ID не найден."]

    referral_rows = report_data["referrals"]
    paid_count = report_data["paid_count"]
    bonus_days = report_data["bonus_days"]
    conversion = (paid_count / len(referral_rows) * 100) if referral_rows else 0
    invited_by = report_data.get("invited_by")
    link = report_data.get("link")
    own_referral_type = get_referral_type_display_name(
        getattr(referrer, "referral_type", None)
    )
    referral_username = getattr(referrer, "username", None) or str(
        referrer.telegram_id
    )

    lines = [
        "🤝 <b>РЕФЕРАЛЬНАЯ СТАТИСТИКА</b>",
        f"Пользователь: {format_admin_user(referrer)}",
        f"Сам приглашен: {format_admin_user(invited_by) if invited_by else 'нет'}",
        f"Его тип входа: <b>{escape(own_referral_type)}</b>",
        (
            f"Уникальная ссылка: <b>выпущена {link.created_at:%d.%m.%Y}</b>"
            if link is not None
            else "Уникальная ссылка: <b>не выпускалась</b>"
        ),
        "",
        f"Всего приглашено: <b>{len(referral_rows)}</b>",
        f"Перешли на платный: <b>{paid_count}</b> ({conversion:.1f}%)",
        f"Успешных оплат: <b>{report_data.get('payments_count', 0)}</b>",
        f"Сумма оплат рефералов: <b>{report_data.get('payments_sum', 0)} ₽</b>",
        f"Начислено бонусов: <b>{bonus_days} дн.</b>",
        "",
    ]

    if link is not None:
        lines.insert(
            5,
            f"Ссылка: <code>{TELEGRAM_BOT_URL}?start=s{escape(referral_username)}</code>",
        )

    referral_type_stats = report_data.get("referral_type_stats", {})
    if referral_type_stats:
        lines.append("<b>Типы реферальной программы:</b>")
        for referral_type, count in sorted(
            referral_type_stats.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            lines.append(f"- {escape(referral_type)}: <b>{count}</b>")
        lines.append("")

    tariff_stats = report_data.get("tariff_stats", {})
    if tariff_stats:
        lines.append("<b>Оплаты по тарифам:</b>")
        for tariff_name, stats in sorted(
            tariff_stats.items(),
            key=lambda item: get_tariff_order(item[0]),
        ):
            lines.append(
                f"- {escape(tariff_name)}: "
                f"<b>{stats['payments_count']}</b> / "
                f"<b>{stats['payments_sum']} ₽</b>"
            )
        lines.append("")

    bonus_stats = report_data.get("bonus_stats", {})
    if bonus_stats:
        lines.append("<b>Начисленные бонусы:</b>")
        for bonus_type, days in bonus_stats.items():
            lines.append(f"- {escape(bonus_type)}: <b>{days} дн.</b>")
        lines.append("")

    if not referral_rows:
        lines.append("<i>Этот пользователь пока никого не пригласил.</i>")
        return ["\n".join(lines)]

    lines.append("<b>Список приглашенных:</b>")

    for index, row in enumerate(referral_rows[:30], 1):
        referral = row["user"]
        first_seen = row["first_seen"]
        first_seen_text = (
            first_seen.strftime("%d.%m.%Y") if first_seen else "нет даты"
        )
        paid_text = (
            f"{row['payments_count']} оплат / {row['payments_sum']} ₽"
            if row["payments_count"] > 0
            else "нет оплат"
        )

        lines.append(
            f"{index}. {format_admin_user(referral)} | "
            f"{first_seen_text} | "
            f"{escape(row.get('referral_type', 'Не указан'))} | "
            f"{paid_text} | "
            f"бонус {row['bonus_days']} дн."
        )

    if len(referral_rows) > 30:
        lines.append(f"\n<i>Показаны первые 30 из {len(referral_rows)}.</i>")

    return split_message("\n".join(lines))


@service_router.message(F.text.startswith("/ref-paid-pending"), IsAdmin())
async def __on_referral_paid_pending_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    limit = 30

    if args:
        try:
            limit = int(args[0])
            if limit < 1 or limit > 100:
                raise ValueError
        except ValueError:
            await message.answer(
                "❌ Лимит должен быть числом от 1 до 100.\n"
                "Пример: <code>/ref-paid-pending 50</code>"
            )
            return

    async with tx(session_maker) as session:
        rows = await get_pending_referral_purchase_bonus_rows(
            session=session,
            limit=limit,
        )

    for report_message in generate_pending_referral_purchase_bonus_messages(rows):
        await message.answer(text=report_message)
        await asyncio.sleep(0.5)


async def get_pending_referral_purchase_bonus_rows(session, limit: int) -> list[dict]:
    referral_user = aliased(User)
    referrer_user = aliased(User)
    purchase_bonus = aliased(ReferralBonus)

    payments_subquery = (
        select(
            YkPayment.user_id.label("user_id"),
            func.count(YkPayment.id).label("payments_count"),
            func.coalesce(func.sum(YkPayment.amount), 0).label("payments_sum"),
            func.min(YkPayment.created_at).label("first_payment_at"),
            func.max(YkPayment.created_at).label("last_payment_at"),
        )
        .where(YkPayment.status == "succeeded")
        .group_by(YkPayment.user_id)
        .subquery()
    )

    result = await session.execute(
        select(
            referral_user,
            referrer_user,
            payments_subquery.c.payments_count,
            payments_subquery.c.payments_sum,
            payments_subquery.c.first_payment_at,
            payments_subquery.c.last_payment_at,
        )
        .join(referrer_user, referral_user.referred_by_id == referrer_user.id)
        .join(payments_subquery, payments_subquery.c.user_id == referral_user.id)
        .outerjoin(
            purchase_bonus,
            and_(
                purchase_bonus.referral_id == referral_user.id,
                purchase_bonus.bonus_type == ReferralBonusType.PURCHASE,
            ),
        )
        .where(
            referral_user.referred_by_id.is_not(None),
            referral_user.referral_type == ReferralType.STANDARD,
            purchase_bonus.id.is_(None),
        )
        .order_by(payments_subquery.c.first_payment_at.desc())
        .limit(limit)
    )

    return [
        {
            "referral": referral,
            "referrer": referrer,
            "payments_count": payments_count or 0,
            "payments_sum": payments_sum or 0,
            "first_payment_at": first_payment_at,
            "last_payment_at": last_payment_at,
        }
        for (
            referral,
            referrer,
            payments_count,
            payments_sum,
            first_payment_at,
            last_payment_at,
        ) in result.all()
    ]


def generate_pending_referral_purchase_bonus_messages(rows: list[dict]) -> list[str]:
    lines = [
        "💸 <b>ОПЛАТИВШИЕ РЕФЕРАЛЫ БЕЗ БОНУСА</b>",
        "",
    ]

    if not rows:
        lines.append(
            "<i>Нет оплативших рефералов, которым еще не отмечен бонус за покупку.</i>"
        )
        return ["\n".join(lines)]

    lines.append(
        "Чтобы начислить дни пригласившему: "
        "<code>/ref-award-paid referral_tg_id days</code>"
    )
    lines.append("Пример: <code>/ref-award-paid 123456789 30</code>")
    lines.append("")

    for index, row in enumerate(rows, 1):
        referral = row["referral"]
        referrer = row["referrer"]
        first_payment = row["first_payment_at"]
        first_payment_text = (
            first_payment.strftime("%d.%m.%Y") if first_payment else "нет даты"
        )

        lines.append(
            f"{index}. Оплатил: {format_admin_user(referral)} | "
            f"{row['payments_count']} оплат / {row['payments_sum']} ₽ | "
            f"первая {first_payment_text}"
        )
        lines.append(f"   Пригласил: {format_admin_user(referrer)}")

    return split_message("\n".join(lines))


@service_router.message(F.text.startswith("/ref-award-paid"), IsAdmin())
async def __on_referral_paid_bonus_award_requested(
    message: Message,
    rwms_client: RwmsClient,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды.\n"
            "Используйте: <code>/ref-award-paid referral_tg_id days</code>\n"
            "Пример: <code>/ref-award-paid 123456789 30</code>"
        )
        return

    try:
        referral_tg_id = int(args[0])
        days = int(args[1])
        if days < 1:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ referral_tg_id и days должны быть положительными числами."
        )
        return

    interval = timedelta(days=days)

    async with tx(session_maker) as session:
        result = await referral_rewards.award_referral_purchase_bonus(
            session=session,
            rwms_client=rwms_client,
            config=config,
            referral_tg_id=referral_tg_id,
            interval=interval,
            referral_type=ReferralType.STANDARD,
        )

    if result["status"] == "referral_not_found":
        await message.answer("❌ Оплативший реферал с таким Telegram ID не найден.")
        return
    if result["status"] == "no_referrer":
        await message.answer("❌ Этот пользователь не привязан к пригласившему.")
        return
    if result["status"] == "no_payment":
        await message.answer("❌ У этого реферала нет успешной оплаты.")
        return
    if result["status"] == "already_awarded":
        await message.answer("ℹ️ Бонус за покупку этого реферала уже отмечен.")
        return
    if result["status"] == "wrong_referral_type":
        await message.answer(
            "❌ Этот пользователь пришел не по обычной реферальной ссылке."
        )
        return
    if result["status"] == "rwms_referrer_not_found":
        await message.answer(
            "❌ Пригласивший найден в БД, но не найден в RWMS. Дни не начислены."
        )
        return
    if result["status"] == "rwms_update_failed":
        await message.answer("❌ Не удалось обновить подписку пригласившего в RWMS.")
        return

    referrer = result["referrer"]
    referral = result["referral"]
    await message.answer(
        "✅ Бонус начислен.\n"
        f"Пригласивший: {format_admin_user(referrer)}\n"
        f"Оплативший реферал: {format_admin_user(referral)}\n"
        f"Добавлено: <b>{days} дн.</b>"
    )


@service_router.message(F.text.startswith("/statinterval"), IsAdmin())
async def __on_stat_interval_requested(
    message: Message,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    # Парсим аргументы команды
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат команды. Используйте: /statinterval начальная_дата конечная_дата\n"
            "Пример: /statinterval 2024-01-01 2024-01-31\n"
            "Формат даты: ГГГГ-ММ-ДД"
        )
        return

    start_date = None
    end_date = None

    try:
        start_date = datetime.strptime(args[0], "%Y-%m-%d").date()
        end_date = datetime.strptime(args[1], "%Y-%m-%d").date()

        # Проверяем, что начальная дата не больше конечной
        if start_date > end_date:
            await message.answer("❌ Начальная дата не может быть больше конечной даты")
            return

    except ValueError as e:
        await message.answer(
            "❌ Неверный формат даты. Используйте формат: ГГГГ-ММ-ДД\n"
            "Пример: /statinterval 2024-01-01 2024-01-31"
        )
        return

    # Отправляем сообщение о начале обработки
    processing_msg = await message.answer("🔄 Собираем статистику...")

    try:
        async with tx(session_maker) as session:
            # Получаем статистику по подпискам и платежам
            subscription_stats = await get_subscription_payment_stats_by_interval(
                session=session,
                rwms_client=rwms_client,
                start_date=start_date,
                end_date=end_date,
            )

        # Формируем отчет (теперь возвращает список сообщений)
        report_messages = await generate_interval_report(
            subscription_stats, start_date, end_date
        )

        # Удаляем сообщение о обработке
        await processing_msg.delete()

        # Отправляем все части отчета
        for i, report_part in enumerate(report_messages, 1):
            if len(report_part) > 4096:
                # Если какая-то часть все еще слишком длинная, разбиваем ее
                chunks = split_message(report_part)
                for chunk in chunks:
                    await message.answer(text=chunk)
                    await asyncio.sleep(0.5)  # Небольшая задержка между сообщениями
            else:
                await message.answer(text=report_part)
                if i < len(report_messages):  # Небольшая задержка между сообщениями
                    await asyncio.sleep(0.5)

    except Exception as e:
        await processing_msg.delete()
        await message.answer(f"❌ Ошибка при формировании отчета: {str(e)}")
        logging.exception("Error generating interval report")


def rwms_user_has_connection(rw_user) -> bool:
    """Returns whether Remnawave has evidence that the user actually used traffic."""
    if rw_user.HasField("first_connected"):
        return True

    return (
        getattr(rw_user, "used_traffic_bytes", 0) > 0
        or getattr(rw_user, "lifetime_used_traffic_bytes", 0) > 0
    )


async def get_connected_user_ids_from_rwms(
    rwms_client: RwmsClient,
    username_to_user_id: dict[str, int],
) -> set[int] | None:
    if not username_to_user_id:
        return set()

    connected_user_ids = set()
    offset = 0
    page_size = 1000

    try:
        while True:
            rwms_users = await rwms_client.get_all_users(offset=offset, count=page_size)
            if rwms_users is None:
                return None

            for rw_user in rwms_users.users:
                user_id = username_to_user_id.get(rw_user.username)
                if user_id is not None and rwms_user_has_connection(rw_user):
                    connected_user_ids.add(user_id)

            users_count = len(rwms_users.users)
            if users_count == 0:
                break

            offset += users_count
            total = int(getattr(rwms_users, "total", 0) or 0)
            if total > 0 and offset >= total:
                break

        return connected_user_ids
    except Exception:
        logging.exception("failed to collect connected users from RWMS")
        return None


async def get_connected_user_ids_from_events(session, user_ids: list[int]) -> set[int]:
    if not user_ids:
        return set()

    connections_query = select(EventLog.user_id).where(
        and_(
            EventLog.event_type == "traffic_threshold_reached",
            EventLog.user_id.in_(user_ids),
            EventLog.event_payload["threshold"].astext.cast(Integer) == 0,
        )
    )

    connections_result = await session.execute(connections_query)
    return set(connections_result.scalars().all())


async def get_subscription_payment_stats_by_interval(
    session,
    rwms_client: RwmsClient,
    start_date: datetime.date,
    end_date: datetime.date,
) -> dict:
    """Получает статистику по подпискам и платежам за указанный интервал"""

    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    events_query = (
        select(EventLog.user_id, EventLog.event_payload, User.username)
        .join(User, User.id == EventLog.user_id)
        .where(
            and_(
                EventLog.event_type == "subscription_created",
                EventLog.timestamp >= start_datetime,
                EventLog.timestamp <= end_datetime,
            )
        )
    )

    result = await session.execute(events_query)
    rows = result.all()
    username_to_user_id = {row.username: row.user_id for row in rows if row.username}
    connected_user_ids = await get_connected_user_ids_from_rwms(
        rwms_client=rwms_client,
        username_to_user_id=username_to_user_id,
    )

    user_ids_by_traffic = {}
    for row in rows:
        user_id = row.user_id
        payload = row.event_payload
        traffic_source = payload.get("traffic_source")

        if traffic_source not in user_ids_by_traffic:
            user_ids_by_traffic[traffic_source] = set()

        user_ids_by_traffic[traffic_source].add(user_id)

    stat = {}

    for traffic_source, users_set in user_ids_by_traffic.items():
        if not users_set:
            continue

        users_list = list(users_set)

        payments_query = (
            select(
                YkPayment.subscription_period, func.count(YkPayment.id).label("count")
            )
            .where(
                and_(
                    YkPayment.status == "succeeded",
                    YkPayment.user_id.in_(users_list),
                    YkPayment.created_at >= start_datetime,
                    YkPayment.created_at <= end_datetime,
                )
            )
            .group_by(YkPayment.subscription_period)
        )

        payments_result = await session.execute(payments_query)
        payments_by_tariff = payments_result.all()

        unique_paying_users_query = select(
            func.count(func.distinct(YkPayment.user_id))
        ).where(
            and_(
                YkPayment.status == "succeeded",
                YkPayment.user_id.in_(users_list),
                YkPayment.created_at >= start_datetime,
                YkPayment.created_at <= end_datetime,
            )
        )

        unique_paying_users_result = await session.execute(unique_paying_users_query)
        unique_paying_users_count = unique_paying_users_result.scalar() or 0

        if connected_user_ids is None:
            source_connected_user_ids = await get_connected_user_ids_from_events(
                session=session,
                user_ids=users_list,
            )
        else:
            source_connected_user_ids = users_set & connected_user_ids

        connections_count = len(source_connected_user_ids)
        total_payments = sum(count for _, count in payments_by_tariff)

        tariff_stats = {}
        for tariff_id, count in payments_by_tariff:
            tariff_name = get_tariff_display_name(tariff_id)
            tariff_stats[tariff_name] = count

        sub_created_count = len(users_set)

        payments_conversion_rate = (
            (unique_paying_users_count / sub_created_count * 100)
            if sub_created_count > 0
            else 0
        )

        connections_conversion_rate = (
            (connections_count / sub_created_count * 100)
            if sub_created_count > 0
            else 0
        )

        stat[traffic_source] = {
            "subscriptions_created": sub_created_count,
            "successful_payments": total_payments,
            "payments_conversion_rate": payments_conversion_rate,
            "connections_conversion_rate": connections_conversion_rate,
            "tariff_stats": tariff_stats,
            "unique_paying_users": unique_paying_users_count,
            "connections": connections_count,
        }

    return stat


def get_tariff_display_name(tariff_id: str) -> str:
    """Преобразует идентификатор тарифа в читаемое название"""
    tariff_names = {
        "threedays": "3 дня",
        "oneday": "1 день",
        "month": "1 месяц",
        "threemonths": "3 месяца",
        "sixmonths": "6 месяцев",
        "year": "1 год",
    }
    return tariff_names.get(tariff_id, tariff_id)


def get_tariff_order(tariff_name: str) -> int:
    """Возвращает порядок сортировки для тарифов"""
    order = {
        "3 дня": 1,
        "1 день": 2,
        "1 неделя": 3,
        "1 месяц": 4,
        "3 месяца": 5,
        "6 месяцев": 6,
        "1 год": 7,
    }
    return order.get(tariff_name, 99)


INTERVAL_REPORT_TARIFF_COLUMNS = [
    ("1д", "1 день"),
    ("3д", "3 дня"),
    ("1м", "1 месяц"),
    ("3м", "3 месяца"),
    ("1г", "1 год"),
]


def generate_daily_table_report(
    subscription_stats: dict, start_date: datetime.date, end_date: datetime.date
) -> str:
    """Генерирует дневной отчет за интервал дат."""

    lines = [
        f"📊 <b>СТАТИСТИКА ПО ДНЯМ {start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}</b>",
    ]

    for current_date in iter_report_dates(start_date, end_date):
        day_stats = subscription_stats[current_date]
        entered = len(day_stats["entered_bot_user_ids"])
        connected = len(day_stats["connected_user_ids"])
        paid_users = len(day_stats["paid_user_ids"])
        payments_count = day_stats["payments_count"]
        payments_sum = day_stats["payments_sum"]
        not_renewed = len(day_stats["not_renewed_user_ids"])
        tariff_counts = [
            day_stats["tariff_stats"].get(tariff_name, 0)
            for _, tariff_name in INTERVAL_REPORT_TARIFF_COLUMNS
        ]

        lines.extend(
            [
                "",
                f"<b>{current_date:%d.%m.%Y}</b>",
                f"- Зашло: <b>{entered}</b>",
                f"- Подключилось: <b>{connected}</b>",
                f"- Оплатили пользователей: <b>{paid_users}</b>",
                f"- Платежей: <b>{payments_count}</b>",
                f"- Не продлили: <b>{not_renewed}</b>",
                "- Оплаты по тарифам:",
            ]
        )
        for (tariff_label, _), tariff_count in zip(
            INTERVAL_REPORT_TARIFF_COLUMNS, tariff_counts
        ):
            lines.append(f"  - {tariff_label}: <b>{tariff_count}</b>")
        lines.append(f"- Сумма оплат: <b>{payments_sum} ₽</b>")

    return "\n".join(lines)


async def generate_interval_report(
    subscription_stats: dict, start_date: datetime.date, end_date: datetime.date
) -> list[str]:
    """Генерирует отчет за интервал дат, возвращает список сообщений"""

    messages = []

    report_part1 = f"📊 ОТЧЕТ ЗА ПЕРИОД: {start_date} - {end_date}\n\n"
    report_part1 += "💰 СТАТИСТИКА ПО ПОДПИСКАМ И ПЛАТЕЖАМ:\n\n"

    total_connections = 0
    total_subscriptions = 0
    total_payments = 0
    total_unique_paying_users = 0
    total_tariff_stats = {}

    sorted_stats = sorted(
        subscription_stats.items(),
        key=lambda x: x[1]["subscriptions_created"],
        reverse=True,
    )

    for traffic_source, stats in sorted_stats:
        ts_display = "Direct" if traffic_source is None else f"TS_{traffic_source}"
        subscriptions = stats["subscriptions_created"]
        payments = stats["successful_payments"]
        payments_conversion = stats["payments_conversion_rate"]
        connections_conversion = stats["connections_conversion_rate"]
        tariff_stats = stats.get("tariff_stats", {})
        unique_paying_users = stats["unique_paying_users"]
        connections_count = stats["connections"]

        report_part1 += f"🔹 {ts_display}:\n"
        report_part1 += f"   👥 Подписок: {subscriptions}\n"
        report_part1 += f"   🔌 Подключений: {connections_count}\n"
        report_part1 += f"   🎫 Покупателей: {unique_paying_users}\n"
        report_part1 += f"   💰 Платежей: {payments}\n"

        if tariff_stats:
            for tariff_name, count in tariff_stats.items():
                report_part1 += f"             {tariff_name}: {count}\n"
                total_tariff_stats[tariff_name] = (
                    total_tariff_stats.get(tariff_name, 0) + count
                )

        report_part1 += (
            f"   📈 Конверсия в подключения: {connections_conversion:.1f}%\n"
        )
        report_part1 += f"   📈 Конверсия в продажи: {payments_conversion:.1f}%\n\n"

        total_connections += connections_count
        total_subscriptions += subscriptions
        total_payments += payments
        total_unique_paying_users += unique_paying_users

    total_payments_conversion = (
        (total_unique_paying_users / total_subscriptions * 100)
        if total_subscriptions > 0
        else 0
    )

    total_connections_conversion = (
        (total_connections / total_subscriptions * 100)
        if total_subscriptions > 0
        else 0
    )

    report_part1 += f"📊 ОБЩИЕ ИТОГИ:\n"
    report_part1 += f"   👥 Всего подписок: {total_subscriptions}\n"
    report_part1 += f"   🔌 Всего подключений: {total_connections}\n"
    report_part1 += f"   🎫 Всего покупателей: {total_unique_paying_users}\n"
    report_part1 += f"   💰 Всего платежей: {total_payments}\n"

    if total_tariff_stats:
        sorted_total_tariffs = sorted(
            total_tariff_stats.items(), key=lambda x: get_tariff_order(x[0])
        )
        for tariff_name, count in sorted_total_tariffs:
            report_part1 += f"             {tariff_name}: {count}\n"

    report_part1 += (
        f"   📈 Общая конверсия в подключения: {total_connections_conversion:.1f}%\n"
    )
    report_part1 += (
        f"   📈 Общая конверсия в продажи: {total_payments_conversion:.1f}%\n"
    )

    messages.append(report_part1)

    if len(sorted_stats) > 1:
        report_part2 = f"📈 АНАЛИТИКА ПО ИСТОЧНИКАМ ({start_date} - {end_date}):\n\n"

        sources_with_conversion = [
            (ts, stats)
            for ts, stats in sorted_stats
            if stats["subscriptions_created"] > 0
        ]

        if sources_with_conversion:
            best_source = max(
                sources_with_conversion, key=lambda x: x[1]["payments_conversion_rate"]
            )
            best_ts_display = (
                "Direct" if best_source[0] is None else f"TS_{best_source[0]}"
            )

            sources_with_positive_conversion = [
                s
                for s in sources_with_conversion
                if s[1]["payments_conversion_rate"] > 0
            ]
            if len(sources_with_positive_conversion) > 1:
                worst_source = min(
                    sources_with_positive_conversion,
                    key=lambda x: x[1]["payments_conversion_rate"],
                )
            else:
                worst_source = best_source

            worst_ts_display = (
                "Direct" if worst_source[0] is None else f"TS_{worst_source[0]}"
            )

            report_part2 += f"🏆 Лучшая конверсия:\n"
            report_part2 += f"   {best_ts_display} - {best_source[1]['payments_conversion_rate']:.1f}%\n"
            report_part2 += f"   ({best_source[1]['subscriptions_created']} подписок, {best_source[1]['unique_paying_users']} покупателей)\n\n"

            if best_source != worst_source:
                report_part2 += f"📉 Худшая конверсия:\n"
                report_part2 += f"   {worst_ts_display} - {worst_source[1]['payments_conversion_rate']:.1f}%\n"
                report_part2 += f"   ({worst_source[1]['subscriptions_created']} подписок, {worst_source[1]['unique_paying_users']} покупателей)\n\n"

        report_part2 += "💡 РЕКОМЕНДАЦИИ:\n"

        if total_payments_conversion < 10:
            report_part2 += "• Низкая конверсия - стоит улучшить онбординг\n"
        elif total_payments_conversion > 30:
            report_part2 += "• Отличная конверсия! Продолжайте в том же духе\n"

        zero_conversion_sources = [
            ts
            for ts, stats in sorted_stats
            if stats["subscriptions_created"] > 0
            and stats["payments_conversion_rate"] == 0
        ]
        if zero_conversion_sources:
            report_part2 += (
                f"• {len(zero_conversion_sources)} источников без конверсии - нужен анализ\n"
            )

        messages.append(report_part2)

    return messages


def split_message(text: str, max_length: int = 4096) -> list[str]:
    """Разбивает длинное сообщение на части"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Ищем последний перенос строки в пределах лимита
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            # Если нет переносов, разбиваем по пробелу
            split_pos = text.rfind(" ", 0, max_length)
        if split_pos == -1:
            # Если нет пробелов, просто обрезаем
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()

    return chunks


@service_router.message(F.text.startswith("/recurrents"), IsAdmin())
async def __on_recurrents_info_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    stats = {
        "total_sum": 0,
        "total_count": 0,
        "trial_count": 0,
        "tariffs": {
            "1 день": 0,
            "1 месяц": 0,
            "3 месяца": 0,
            "6 месяцев": 0,
            "1 год": 0,
        },
    }

    async with session_maker() as session:
        subscriptions = await get_all_recurrents(session)
        stats["total_count"] = len(subscriptions)

        for sub in subscriptions:
            if sub.is_trial_promotion:
                stats["trial_count"] += 1

            period = sub.subscription_period

            if period == OneDayTariff().db_tariff_id:
                stats["total_sum"] += sub.amount * 30
                stats["tariffs"]["1 день"] += 1
            elif period == OneMonthTariff().db_tariff_id:
                stats["total_sum"] += sub.amount
                stats["tariffs"]["1 месяц"] += 1
            elif period == ThreeMonthsTariff().db_tariff_id:
                stats["total_sum"] += sub.amount / 3
                stats["tariffs"]["3 месяца"] += 1
            elif period == SixMonthsTariff().db_tariff_id:
                stats["total_sum"] += sub.amount / 6
                stats["tariffs"]["6 месяцев"] += 1
            elif period == OneYearTariff().db_tariff_id:
                stats["total_sum"] += sub.amount / 12
                stats["tariffs"]["1 год"] += 1

    report_text = generate_recurrents_report(stats)
    await message.answer(text=report_text)


def generate_recurrents_report(stats: dict) -> str:
    """Генерирует структурированный отчет по рекуррентным платежам"""

    report = "🔄 <b>АНАЛИТИКА РЕКУРРЕНТНЫХ ПОДПИСОК</b>\n\n"

    report += "📊 <b>ОБЩИЕ ПОКАЗАТЕЛИ:</b>\n"
    report += f"   👥 Всего клиентов с автоплатежами: {stats['total_count']}\n"
    report += f"   🎁 Из них пробных подписок: {stats['trial_count']}\n"
    report += f"   💰 Прогноз MRR (выручка в месяц): {int(stats['total_sum']):,} ₽\n\n".replace(
        ",", " "
    )

    report += "🎫 <b>РАЗБИВКА ПО ТАРИФАМ:</b>\n"

    # Иконки для красоты
    icons = {
        "1 день": "⚡️",
        "1 месяц": "📅",
        "3 месяца": "🗓",
        "6 месяцев": "⏳",
        "1 год": "🏆",
    }

    for tariff_name, count in stats["tariffs"].items():
        if count > 0:
            icon = icons.get(tariff_name, "🔹")
            report += f"   {icon} {tariff_name}: {count} шт.\n"

    report += "\n💡 <i>Примечание: MRR рассчитывается как сумма всех подписок, приведенная к 1 месяцу.</i>"

    return report


@service_router.message(F.text.startswith("/payments"), IsAdmin())
async def __on_user_payments_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:]
    if not args:
        await message.answer(
            "❌ Введите ID пользователя. Пример: <code>/payments 12345678</code>"
        )
        return

    try:
        target_tg_id = int(args[0])
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return

    async with session_maker() as session:
        # 1. Получаем пользователя
        user_query = select(User).where(User.telegram_id == target_tg_id)
        user_result = await session.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer(
                f"❌ Пользователь с TG ID {target_tg_id} не найден в базе."
            )
            return

        # 2. Получаем все платежи
        payments_query = (
            select(YkPayment)
            .where(YkPayment.user_id == user.id)
            .order_by(YkPayment.created_at.desc())
        )
        payments_result = await session.execute(payments_query)
        payments = payments_result.scalars().all()

        # 3. Проверяем статус автоплатежа
        recurrent_query = select(YkRecurrentPayment).where(
            YkRecurrentPayment.user_id == user.id
        )
        recurrent_result = await session.execute(recurrent_query)
        recurrent = recurrent_result.scalar_one_or_none()

        # 4. ДОПОЛНИТЕЛЬНАЯ АНАЛИТИКА
        # Считаем LTV (сумму всех успешных платежей)
        ltv_query = select(func.sum(YkPayment.amount)).where(
            YkPayment.user_id == user.id, YkPayment.status == "succeeded"
        )
        ltv_res = await session.execute(ltv_query)
        total_ltv = ltv_res.scalar() or 0

        # Прогресс трафика (подключался ли вообще)
        traffic_query = select(UserTrafficProgress).where(
            UserTrafficProgress.user_id == user.id
        )
        traffic_res = await session.execute(traffic_query)
        traffic_progress = traffic_res.scalar_one_or_none()

        # Дата первого появления в системе (самый ранний лог)
        first_seen_query = select(func.min(EventLog.timestamp)).where(
            EventLog.user_id == user.id
        )
        first_seen_res = await session.execute(first_seen_query)
        first_seen = first_seen_res.scalar()

    # Формируем и отправляем отчет (передаем все собранные данные)
    report_text = generate_user_payments_report(
        user=user,
        payments=payments,
        recurrent=recurrent,
        ltv=total_ltv,
        traffic=traffic_progress,
        first_seen=first_seen,
    )
    await message.answer(text=report_text)


def generate_user_payments_report(
    user: User,
    payments: list[YkPayment],
    recurrent: YkRecurrentPayment,
    ltv: int,
    traffic: UserTrafficProgress,
    first_seen: datetime,
) -> str:
    """Генерирует детальный отчет по платежам и поведению пользователя"""

    status_emoji = "✅" if recurrent else "❌"
    recurrent_status = "Активен" if recurrent else "Выключен/Нет"

    report = f"👤 <b>ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ</b>\n"
    report += (
        f"👤 Юзер: {format_admin_user(user)}\n"
    )

    # Секция подписки
    expire_str = user.expire_at.strftime("%d.%m.%Y") if user.expire_at else "Нет данных"
    report += f"📅 Истекает: <b>{expire_str}</b>\n"
    report += f"{status_emoji} <b>Автоплатеж:</b> {recurrent_status}\n"

    if recurrent:
        report += (
            f"   💳 Тариф: {get_tariff_display_name(recurrent.subscription_period)}\n"
        )
        report += f"   💰 Сумма: {recurrent.amount} {recurrent.currency}\n"

    # Секция аналитики
    report += "\n📈 <b>АНАЛИТИКА:</b>\n"
    report += f"   💰 Суммарный LTV: <b>{ltv} ₽</b>\n"

    if traffic:
        # Определяем статус по флагам из UserTrafficProgress
        if traffic.passed_100mb:
            t_status = "🔥 Активный (100MB+)"
        elif traffic.passed_5mb:
            t_status = "📡 Подключен (5MB+)"
        elif traffic.passed_0:
            t_status = "🔌 Конфиг скачан (0+)"
        else:
            t_status = "⚪️ Не подключался"
        report += f"   📊 Использование: {t_status}\n"

    if first_seen:
        days_on = (datetime.now() - first_seen).days
        report += f"   ⏳ Зарегистрирован с: {first_seen.strftime('%d.%m.%Y')} ({days_on} дн.)\n"

    # Секция истории платежей
    report += "\n📜 <b>ИСТОРИЯ ОПЕРАЦИЙ:</b>\n"

    if not payments:
        report += "   <i>Платежей не найдено</i>\n"
    else:
        # Берем последние 10 платежей, чтобы сообщение не было слишком длинным
        for p in payments[:10]:
            date_str = p.created_at.strftime("%d.%m.%Y %H:%M")
            p_status = "🟢" if p.status == "succeeded" else "🔴"
            trial_mark = " (🎁 Trial)" if p.is_trial_promotion else ""

            report += f"   {p_status} {date_str} — <b>{p.amount} {p.currency}</b>\n"
            report += (
                f"      {get_tariff_display_name(p.subscription_period)}{trial_mark}\n"
            )
            report += f"      ID: <code>{escape(p.payment_id)}</code>\n"

    return report


@service_router.message(F.text.startswith("/top-payments"), IsAdmin())
async def __on_top_payments_requested(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    from sqlalchemy import func, select, desc

    async with session_maker() as session:
        # 1. Получаем ТОП-10 пользователей по сумме успешных платежей
        top_ltv_query = (
            select(
                YkPayment.user_id,
                func.sum(YkPayment.amount).label("total_ltv"),
                func.count(YkPayment.id).label("payments_count"),
            )
            .where(YkPayment.status == "succeeded")
            .group_by(YkPayment.user_id)
            .order_by(desc("total_ltv"))
            .limit(10)
        )

        top_res = await session.execute(top_ltv_query)
        top_users_data = top_res.all()

        if not top_users_data:
            await message.answer("Пока нет успешных платежей для формирования топа.")
            return

        report_lines = ["🏆 <b>ТОП-10 КЛИЕНТОВ ПО ПЛАТЕЖАМ</b>\n"]

        for i, row in enumerate(top_users_data, 1):
            user_id, ltv, count = row

            # Подгружаем детали по каждому юзеру из топа
            user_query = select(User).where(User.id == user_id)
            user = (await session.execute(user_query)).scalar_one_or_none()

            if not user:
                continue

            # Проверяем наличие автоплатежа
            rec_query = select(YkRecurrentPayment).where(
                YkRecurrentPayment.user_id == user_id
            )
            recurrent = (await session.execute(rec_query)).scalar_one_or_none()

            # Проверяем трафик
            traffic_query = select(UserTrafficProgress).where(
                UserTrafficProgress.user_id == user_id
            )
            traffic = (await session.execute(traffic_query)).scalar_one_or_none()

            # Формируем блок юзера
            rec_status = "✅" if recurrent else "❌"

            # Короткий статус трафика
            t_icon = "⚪️"
            if traffic:
                if traffic.passed_100mb:
                    t_icon = "🔥"
                elif traffic.passed_0:
                    t_icon = "🔌"

            line = (
                f"{i}. <b>{ltv:,} ₽</b> — <code>{user.telegram_id}</code>\n"
                f"   {t_icon} Использ. | {rec_status} Автоплат. | 💳 Чеков: {count}\n"
                f"   👤 {format_admin_user(user)}\n"
            ).replace(",", " ")

            report_lines.append(line)

        await message.answer(text="\n".join(report_lines))
