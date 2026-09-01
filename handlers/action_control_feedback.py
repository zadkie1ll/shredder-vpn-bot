from html import escape

import sqlalchemy
from aiogram import F
from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from filters.is_admin import IsAdmin
from repositories import action_control_feedback as repo
from repositories import feedback_campaigns as feedback_repo
from utils.config import Config
from utils.sql_helpers import tx

action_control_feedback_router = Router()


def _format_rate(value) -> str:
    if value is None:
        return "0%"
    return f"{float(value):.1f}%"


def _format_date(value) -> str:
    if value is None:
        return "-"
    return value.strftime("%d.%m.%Y %H:%M")


def _format_money(value) -> str:
    if value is None:
        value = 0
    return f"{int(value):,}".replace(",", " ") + " ₽"


def _trim(value: str, limit: int = 400) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _period_name(value: str | None) -> str:
    names = {
        "oneday": "1 день",
        "threedays": "3 дня",
        "month": "месяц",
        "threemonths": "3 месяца",
        "sixmonths": "6 месяцев",
        "year": "год",
        "-": "-",
        None: "-",
    }
    return names.get(value, value or "-")


def _discount_label(row: dict) -> str:
    if row.get("selected_discount_percent"):
        return f"-{row['selected_discount_percent']}%"
    if row.get("selected_discount_amount"):
        return f"-{_format_money(row['selected_discount_amount'])}"
    return "-"


@action_control_feedback_router.message(F.text, ~F.text.startswith("/"))
async def collect_action_control_reply(
    message: Message,
    state: FSMContext,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    if message.from_user is None or not message.text:
        raise SkipHandler()

    if await state.get_state():
        raise SkipHandler()

    reply_to_message_id = None
    if message.reply_to_message:
        reply_to_message_id = message.reply_to_message.message_id

    async with tx(session_maker) as session:
        saved = await repo.save_action_control_reply(
            session,
            telegram_id=message.from_user.id,
            message_id=message.message_id,
            text_value=message.text,
            reply_to_message_id=reply_to_message_id,
            bot_instance=config.bot_instance_id,
        )

    if not saved:
        raise SkipHandler()

    await message.answer("Спасибо, я передал обратную связь команде.")


@action_control_feedback_router.message(
    F.text.startswith("/action-feedback") | F.text.startswith("/action-stats"),
    IsAdmin(),
)
async def on_action_feedback_stats(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    days = 30
    if args:
        try:
            days = max(1, min(365, int(args[0])))
        except ValueError:
            await message.answer("Формат: <code>/action-feedback [days]</code>")
            return

    async with tx(session_maker) as session:
        reconciled = await feedback_repo.reconcile_used_feedback_rewards(session)
        stats = await repo.get_action_control_stats(
            session,
            days=days,
            bot_instance=config.bot_instance_id,
        )
        period_stats = await repo.get_action_control_reward_period_stats(
            session,
            days=days,
            bot_instance=config.bot_instance_id,
        )
        purchases = await repo.get_recent_action_control_discount_purchases(
            session,
            days=days,
            limit=7,
            bot_instance=config.bot_instance_id,
        )
        replies = await repo.get_recent_action_control_replies(
            session,
            limit=5,
            bot_instance=config.bot_instance_id,
        )

    period_stats_by_action: dict[str, list[dict]] = {}
    for row in period_stats:
        period_stats_by_action.setdefault(row["action_key"], []).append(row)

    total_sent = sum(row.get("sent_count") or 0 for row in stats)
    total_failed = sum(row.get("failed_count") or 0 for row in stats)
    total_replied = sum(row.get("replied_count") or 0 for row in stats)
    total_selected = sum(row.get("rewards_selected_count") or 0 for row in stats)
    total_used = sum(row.get("rewards_used_count") or 0 for row in stats)
    total_discount_revenue = sum(row.get("discount_revenue") or 0 for row in stats)

    lines = [
        f"<b>Action control за {days} дн.</b>",
        "",
        f"Отправлено: <code>{total_sent}</code>",
        f"Ошибок отправки: <code>{total_failed}</code>",
        f"Ответили: <code>{total_replied}</code> ({_format_rate(100 * total_replied / total_sent if total_sent else 0)})",
        f"Выбрали скидку: <code>{total_selected}</code>",
        f"Купили по скидке: <code>{total_used}</code>",
        f"Выручка по скидкам: <b>{_format_money(total_discount_revenue)}</b>",
    ]
    if reconciled:
        lines.append(f"Обновлено оплат по скидкам: <code>{reconciled}</code>")

    if not stats:
        lines.append("\nПока нет отправленных action-сообщений.")
    else:
        for row in stats:
            lines.extend(
                [
                    "",
                    f"<b>{escape(row['action_key'])}</b>",
                    f"Всего: <code>{row['total_count'] or 0}</code>",
                    f"Отправлено: <code>{row['sent_count'] or 0}</code> · Ошибок: <code>{row['failed_count'] or 0}</code>",
                    f"Ответили: <code>{row['replied_count'] or 0}</code> ({_format_rate(row['reply_rate'])})",
                    f"Скидки: выдано <code>{row['rewards_count'] or 0}</code>, выбрали <code>{row['rewards_selected_count'] or 0}</code> ({_format_rate(row['reward_select_rate'])}), купили <code>{row['rewards_used_count'] or 0}</code> ({_format_rate(row['reward_purchase_rate'])})",
                    f"Выручка по скидкам: <b>{_format_money(row['discount_revenue'])}</b>",
                    f"Любые оплаты после сообщения: <code>{row['payments_after_count'] or 0}</code> / {_format_money(row['revenue_after'])}",
                ]
            )
            action_period_stats = period_stats_by_action.get(row["action_key"], [])
            if action_period_stats:
                lines.append("Тарифы по скидке:")
                for period_row in action_period_stats:
                    lines.append(
                        " · "
                        f"{escape(_period_name(period_row['subscription_period']))}: "
                        f"выбрали <code>{period_row['selected_count'] or 0}</code>, "
                        f"купили <code>{period_row['used_count'] or 0}</code>, "
                        f"{_format_money(period_row['revenue'])}"
                    )

    if purchases:
        lines.append("\n<b>Последние покупки по скидке</b>")
        for row in purchases:
            username = row.get("username") or "-"
            lines.extend(
                [
                    "",
                    f"{_format_date(row['paid_at'])} · <code>{row['telegram_id']}</code> · {escape(username)}",
                    f"{escape(row['action_key'])}: {escape(_period_name(row['selected_subscription_period']))} · {_discount_label(row)} · <b>{_format_money(row['amount'])}</b>",
                ]
            )

    if replies:
        lines.append("\n<b>Последние ответы</b>")
        for row in replies:
            username = row.get("username") or "-"
            lines.extend(
                [
                    "",
                    f"{_format_date(row['created_at'])} · <code>{row['telegram_id_snapshot']}</code> · {escape(username)}",
                    f"{escape(row['action_key'])}: {escape(_trim(row['text_value']))}",
                ]
            )

    await message.answer("\n".join(lines))
