import logging
from functools import wraps
from aiogram import Bot
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import User
from aiogram.types import Message
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

import proto.rwmanager_pb2 as proto
from utils.config import Config
from utils.sql_helpers import tx
from utils.sql_helpers import add_event_log
from utils.sql_helpers import get_user_by_telegram_id
from utils.redis_message_broker import RedisMessageBroker
from common.models.db import User
from common.models.messages import ConversionEvent
from common.models.messages import SendConversionMessage
from common.models.analytics_event import AnalyticsEvent


async def send_analytics_event(
    session_maker: async_sessionmaker, telegram_id: int, event: AnalyticsEvent
) -> User:
    async with tx(session_maker) as session:
        return await send_analytics_event_with_session(session, telegram_id, event)


async def send_analytics_event_with_session(
    session: AsyncSession, telegram_id: int, event: AnalyticsEvent
) -> User:
    db_user = await get_user_by_telegram_id(session, telegram_id)

    if db_user:
        await add_event_log(session, event, db_user.username)
    else:
        logging.error(f"not found user for telegram id {telegram_id}")

    return db_user


async def send_conversion_event(
    config: Config,
    redis_message_broker: RedisMessageBroker,
    event: ConversionEvent,
    database_user: User,
):
    if database_user is None:
        logging.error("can't send conversion event, user is None")
        return

    username = database_user.username
    telegram_id = database_user.telegram_id

    try:
        if telegram_id in config.admins:
            logging.info(f"skip collecting conversion for admin {telegram_id}")
            return

        logging.debug(f"send conversion event {event} for user {telegram_id}")

        msg = SendConversionMessage(
            service="monkey-island-ym-stat",
            type="send-conversion",
            client_id=username,
            event=event,
        )

        await redis_message_broker.push_message_to_ym_stat(message=msg)
    except Exception as e:
        logging.exception(f"failed to send conversion event {event}: {e}")


def get_log_username(user: User) -> str:
    return f"{user.id}({user.username})" if user.username else f"{user.id}"


def send_typing_action(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        user = None
        bot = None

        for arg in list(args) + list(kwargs.values()):
            if isinstance(arg, Message) or isinstance(arg, CallbackQuery):
                user = arg.from_user

            if isinstance(arg, Bot):
                bot = arg

        if not bot or not user:
            return await func(*args, **kwargs)

        async with ChatActionSender.typing(bot=bot, chat_id=user.id):
            return await func(*args, **kwargs)

    return wrapper


def log_function_name(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        user = None

        for arg in list(args) + list(kwargs.values()):
            if isinstance(arg, Message) or isinstance(arg, CallbackQuery):
                user = arg.from_user
                break

        if user:
            logging.info(f"{func.__name__} => {get_log_username(user)}")

        return await func(*args, **kwargs)

    return wrapper


def ymid_from_args(args: str) -> int | None:
    try:
        ymid: int | None = None
        if args:
            lst = args.split("-")
            for arg in lst:
                if arg.startswith("ymid"):
                    ymid = int(arg[len("ymid") :])
                    return ymid
    except Exception:
        return None


def traffic_source_from_args(args: str) -> int | None:
    try:
        traffic_source: int | None = None
        if args:
            lst = args.split("-")
            for arg in lst:
                if arg.startswith("ts"):
                    traffic_source = int(arg[len("ts") :])
                    return traffic_source
    except Exception:
        return None


def referrer_username_from_args(args: str) -> str | None:
    try:
        referrer_username: str | None = None
        if args:
            lst = args.split("-")
            for arg in lst:
                if arg.startswith("a"):
                    referrer_username = arg[len("a") :]
                    return referrer_username
    except Exception:
        return None


def data_limit_reset_strategy_to_str(status: proto.TrafficLimitStrategy) -> str:
    if status == proto.TrafficLimitStrategy.NO_RESET:
        return "без сброса ⏰"
    elif status == proto.TrafficLimitStrategy.DAY:
        return "ежедневно ⏰"
    elif status == proto.TrafficLimitStrategy.WEEK:
        return "еженедельно ⏰"
    elif status == proto.TrafficLimitStrategy.MONTH:
        return "ежемесячно ⏰"
    else:
        return "Неизвестный статус"


def status_to_str(status: proto.UserStatus) -> str:
    if status == proto.UserStatus.ACTIVE:
        return "активна"
    elif status == proto.UserStatus.DISABLED:
        return "отключена"
    elif status == proto.UserStatus.LIMITED:
        return "ограничена"
    elif status == proto.UserStatus.EXPIRED:
        return "истекла"
    else:
        return "неизвестный статус"
