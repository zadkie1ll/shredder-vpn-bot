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

from handlers.broadcast_states import BroadcastStates
from utils.config import Config
from filters.is_admin import IsAdmin

from common.models.db import User
from common.models.db import EventLog
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

from utils.rwms_helpers import update_user
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


TABLE_REPORT_TARIFFS = {
    "month": "249",
    "threemonths": "599",
    "year": "1799",
}


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
            YkPayment.created_at,
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

    daily_stats = {
        current_date: {
            "new_users": 0,
            "trial_users": 0,
            "trial_bounced": 0,
            "tariffs": {
                tariff_price: 0 for tariff_price in TABLE_REPORT_TARIFFS.values()
            },
        }
        for current_date in iter_report_dates(start_date, end_date)
    }

    users = {}
    source_stats = defaultdict(lambda: {"new_users": 0, "paid_users": set()})

    for row in subscription_events:
        traffic_source = row.event_payload.get("traffic_source")
        users[row.user_id] = {
            "created_at": row.timestamp,
            "traffic_source": traffic_source,
        }

        created_date = row.timestamp.date()
        if start_date <= created_date <= end_date:
            daily_stats[created_date]["new_users"] += 1
            source_stats[traffic_source]["new_users"] += 1

    payments_by_user = defaultdict(list)
    for payment in payments:
        payments_by_user[payment.user_id].append(payment)

        payment_date = payment.created_at.date()
        if start_date <= payment_date <= end_date:
            tariff_price = TABLE_REPORT_TARIFFS.get(payment.subscription_period)
            if tariff_price is not None:
                daily_stats[payment_date]["tariffs"][tariff_price] += 1

            user_info = users.get(payment.user_id)
            if user_info is not None and payment.user_id in report_user_ids:
                source_stats[user_info["traffic_source"]]["paid_users"].add(
                    payment.user_id
                )

    for user_id, user_info in users.items():
        created_at = user_info["created_at"]
        trial_ends_at = created_at + timedelta(days=trial_period_days)
        user_payments = payments_by_user.get(user_id, [])

        for current_date in iter_report_dates(start_date, end_date):
            current_day_end = datetime.combine(current_date, time.max)

            has_paid_by_day_end = any(
                payment.created_at <= current_day_end for payment in user_payments
            )

            if created_at.date() <= current_date < trial_ends_at.date():
                if not has_paid_by_day_end:
                    daily_stats[current_date]["trial_users"] += 1

            if trial_ends_at.date() == current_date and not has_paid_by_day_end:
                daily_stats[current_date]["trial_bounced"] += 1

    source_rows = []
    for traffic_source, stats in source_stats.items():
        source_rows.append(
            {
                "source": source_display_name(traffic_source, traffic_sources),
                "new_users": stats["new_users"],
                "paid_users": len(stats["paid_users"]),
            }
        )

    source_rows.sort(key=lambda row: row["new_users"], reverse=True)

    return {
        "daily_stats": daily_stats,
        "source_rows": source_rows,
    }


def generate_table_report_messages(
    report_data: dict,
    start_date: date,
    end_date: date,
    trial_period_days: int,
) -> list[str]:
    daily_stats = report_data["daily_stats"]
    source_rows = report_data["source_rows"]

    daily_lines = [
        f"📋 <b>ТАБЛИЦА ЗА ПЕРИОД {start_date:%d.%m.%Y}-{end_date:%d.%m.%Y}</b>",
        "",
        "<code>Дата | Всего | На пробном | Отскочили | 249 | 599 | 1799",
    ]

    totals = {
        "new_users": 0,
        "trial_users": 0,
        "trial_bounced": 0,
        "tariffs": {
            tariff_price: 0 for tariff_price in TABLE_REPORT_TARIFFS.values()
        },
    }

    for current_date in iter_report_dates(start_date, end_date):
        day_stats = daily_stats[current_date]
        tariff_stats = day_stats["tariffs"]

        daily_lines.append(
            f"{current_date:%d.%m.%Y} | "
            f"{day_stats['new_users']} | "
            f"{day_stats['trial_users']} | "
            f"{day_stats['trial_bounced']} | "
            f"{tariff_stats['249']} | "
            f"{tariff_stats['599']} | "
            f"{tariff_stats['1799']}"
        )

        totals["new_users"] += day_stats["new_users"]
        totals["trial_users"] += day_stats["trial_users"]
        totals["trial_bounced"] += day_stats["trial_bounced"]
        for tariff_price, count in tariff_stats.items():
            totals["tariffs"][tariff_price] += count

    daily_lines.append(
        f"Итог | "
        f"{totals['new_users']} | "
        f"{totals['trial_users']} | "
        f"{totals['trial_bounced']} | "
        f"{totals['tariffs']['249']} | "
        f"{totals['tariffs']['599']} | "
        f"{totals['tariffs']['1799']}</code>"
    )
    daily_lines.append("")
    daily_lines.append(
        f"<i>Пробный период считается как {trial_period_days} дн. от даты /start. "
        "Платные тарифы считаются по успешным платежам в дату платежа.</i>"
    )

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
    else:
        source_lines.append("нет данных | 0 | 0")

    source_lines[-1] = f"{source_lines[-1]}</code>"

    return [
        "\n".join(daily_lines),
        "\n".join(source_lines),
    ]


@service_router.message(F.text.startswith("/statinterval"), IsAdmin())
async def __on_stat_interval_requested(
    message: Message,
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
                session=session, start_date=start_date, end_date=end_date
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


async def get_subscription_payment_stats_by_interval(
    session, start_date: datetime.date, end_date: datetime.date
) -> dict:
    """Получает статистику по подпискам и платежам за указанный интервал"""
    from sqlalchemy import and_, func, select
    from common.models.db import YkPayment, EventLog

    start_datetime = datetime.combine(start_date, time.min)
    end_datetime = datetime.combine(end_date, time.max)

    # Получаем все события создания подписок за период
    events_query = select(EventLog.user_id, EventLog.event_payload).where(
        and_(
            EventLog.event_type == "subscription_created",
            EventLog.timestamp >= start_datetime,
            EventLog.timestamp <= end_datetime,
        )
    )

    result = await session.execute(events_query)
    rows = result.all()

    # Группируем user_id по traffic_source
    user_ids_by_traffic = {}
    for row in rows:
        user_id = row.user_id
        payload = row.event_payload
        traffic_source = payload.get("traffic_source")

        # Используем множество для хранения уникальных user_id
        if traffic_source not in user_ids_by_traffic:
            user_ids_by_traffic[traffic_source] = set()

        user_ids_by_traffic[traffic_source].add(user_id)

    stat = {}

    # Обрабатываем каждую группу traffic_source
    for traffic_source, users_set in user_ids_by_traffic.items():
        if not users_set:  # Пропускаем пустые группы
            continue

        # Преобразуем в список для использования в IN clause
        users_list = list(users_set)

        # Считаем платежи для этой группы пользователей по тарифам
        payments_query = (
            select(
                YkPayment.subscription_period, func.count(YkPayment.id).label("count")
            )
            .where(
                and_(YkPayment.status == "succeeded", YkPayment.user_id.in_(users_list))
            )
            .group_by(YkPayment.subscription_period)
        )

        payments_result = await session.execute(payments_query)
        payments_by_tariff = payments_result.all()

        # Количество уникальных плательщиков из тех, кто пришел в указанный период
        unique_paying_users_query = select(
            func.count(func.distinct(YkPayment.user_id))
        ).where(
            and_(YkPayment.status == "succeeded", YkPayment.user_id.in_(users_list))
        )

        unique_paying_users_result = await session.execute(unique_paying_users_query)
        unique_paying_users_count = unique_paying_users_result.scalar() or 0

        connections_query = select(func.count(func.distinct(EventLog.user_id))).where(
            and_(
                EventLog.event_type == "traffic_threshold_reached",
                EventLog.user_id.in_(users_list),
                EventLog.event_payload["threshold"].astext.cast(Integer) == 0,
            )
        )

        connections_result = await session.execute(connections_query)
        connections_count = connections_result.scalar() or 0

        # Считаем общее количество платежей
        total_payments = sum(count for _, count in payments_by_tariff)

        # Группируем платежи по тарифам
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


async def generate_interval_report(
    subscription_stats: dict, start_date: datetime.date, end_date: datetime.date
) -> list[str]:
    """Генерирует отчет за интервал дат, возвращает список сообщений"""

    messages = []

    # Первое сообщение - заголовок и общая статистика
    report_part1 = f"📊 ОТЧЕТ ЗА ПЕРИОД: {start_date} - {end_date}\n\n"

    # Общая статистика по подпискам и платежам
    report_part1 += "💰 СТАТИСТИКА ПО ПОДПИСКАМ И ПЛАТЕЖАМ:\n\n"

    total_connections = 0
    total_subscriptions = 0
    total_payments = 0
    total_unique_paying_users = 0
    total_tariff_stats = {}  # Собираем общую статистику по тарифам

    # Сортируем по количеству подписок (по убыванию)
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

        # Добавляем разбивку по тарифам
        if tariff_stats:
            for tariff_name, count in tariff_stats.items():
                report_part1 += f"             {tariff_name}: {count}\n"
                # Суммируем в общую статистику
                if tariff_name not in total_tariff_stats:
                    total_tariff_stats[tariff_name] = 0
                total_tariff_stats[tariff_name] += count

        report_part1 += (
            f"   📈 Конверсия в подключения: {connections_conversion:.1f}%\n"
        )
        report_part1 += f"   📈 Конверсия в продажи: {payments_conversion:.1f}%\n\n"

        total_connections += connections_count
        total_subscriptions += subscriptions
        total_payments += payments
        total_unique_paying_users += unique_paying_users

    # Итоговая конверсия, рассчитанная по количеству покупателей
    total_payments_conversion = (
        (total_unique_paying_users / total_subscriptions * 100)
        if total_subscriptions > 0
        else 0
    )

    total_conenctions_conversion = (
        (total_connections / total_subscriptions * 100)
        if total_subscriptions > 0
        else 0
    )

    report_part1 += f"📊 ОБЩИЕ ИТОГИ:\n"
    report_part1 += f"   👥 Всего подписок: {total_subscriptions}\n"
    report_part1 += f"   🔌 Всего подключений: {total_connections}\n"
    report_part1 += f"   🎫 Всего покупателей: {total_unique_paying_users}\n"
    report_part1 += f"   💰 Всего платежей: {total_payments}\n"

    # Добавляем общую разбивку по тарифам
    if total_tariff_stats:
        # Сортируем тарифы по порядку (от коротких к длинным)
        sorted_total_tariffs = sorted(
            total_tariff_stats.items(), key=lambda x: get_tariff_order(x[0])
        )
        for tariff_name, count in sorted_total_tariffs:
            report_part1 += f"             {tariff_name}: {count}\n"

    report_part1 += (
        f"   📈 Общая конверсия в подключения: {total_conenctions_conversion:.1f}%\n"
    )
    report_part1 += (
        f"   📈 Общая конверсия в продажи: {total_payments_conversion:.1f}%\n"
    )

    messages.append(report_part1)

    # Второе сообщение - аналитика и выводы
    if len(sorted_stats) > 1:
        report_part2 = f"📈 АНАЛИТИКА ПО ИСТОЧНИКАМ ({start_date} - {end_date}):\n\n"

        # Находим лучший и худший источник по конверсии
        sources_with_conversion = [
            (ts, stats)
            for ts, stats in sorted_stats
            if stats["subscriptions_created"] > 0
        ]

        if sources_with_conversion:
            # Лучший источник по конверсии
            best_source = max(
                sources_with_conversion, key=lambda x: x[1]["payments_conversion_rate"]
            )
            best_ts_display = (
                "Direct" if best_source[0] is None else f"TS_{best_source[0]}"
            )

            # Худший источник по конверсии (исключая нулевые)
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
            report_part2 += f"   {best_ts_display} - {best_source[1]["payments_conversion_rate"]:.1f}%\n"
            report_part2 += f"   ({best_source[1]["subscriptions_created"]} подписок, {best_source[1]["unique_paying_users"]} покупателей)\n\n"

            if best_source != worst_source:
                report_part2 += f"📉 Худшая конверсия:\n"
                report_part2 += f"   {worst_ts_display} - {worst_source[1]["payments_conversion_rate"]:.1f}%\n"
                report_part2 += f"   ({worst_source[1]["subscriptions_created"]} подписок, {worst_source[1]["unique_paying_users"]} покупателей)\n\n"

        # Рекомендации
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
            report_part2 += f"• {len(zero_conversion_sources)} источников без конверсии - нужен анализ\n"

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
        f"👤 Юзер: <code>{user.telegram_id}</code> (@{user.username or 'unknown'})\n"
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
                f"   👤 @{user.username or 'unknown'}\n"
            ).replace(",", " ")

            report_lines.append(line)

        await message.answer(text="\n".join(report_lines))
