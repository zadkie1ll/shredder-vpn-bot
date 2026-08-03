from html import escape

import sqlalchemy
from aiogram import F
from aiogram import Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from filters.is_admin import IsAdmin
from repositories import action_control_feedback as repo
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


def _trim(value: str, limit: int = 400) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


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
        stats = await repo.get_action_control_stats(
            session,
            days=days,
            bot_instance=config.bot_instance_id,
        )
        replies = await repo.get_recent_action_control_replies(
            session,
            limit=5,
            bot_instance=config.bot_instance_id,
        )

    lines = [f"<b>Action feedback за {days} дн.</b>"]
    if not stats:
        lines.append("\nПока нет отправленных action-сообщений.")
    else:
        for row in stats:
            lines.extend(
                [
                    "",
                    f"<b>{escape(row['action_key'])}</b>",
                    f"Отправлено: <code>{row['sent_count'] or 0}</code>",
                    f"Ответили: <code>{row['replied_count'] or 0}</code> ({_format_rate(row['reply_rate'])})",
                    f"Оплатили после: <code>{row['paid_after_count'] or 0}</code>",
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
