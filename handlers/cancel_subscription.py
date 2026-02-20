import logging
import inspect
import sqlalchemy

from aiogram import F
from aiogram import Router
from aiogram.types import Message
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramForbiddenError

import handlers.markups as markups
from .misc import log_function_name
from .misc import send_typing_action
from .misc import send_conversion_event
from .misc import get_log_username
from .misc import send_analytics_event
from .misc import send_analytics_event_with_session

from utils.config import Config
from utils.redis_message_broker import RedisMessageBroker
from utils.translator import translator as ts
from utils.sql_helpers import tx
from utils.sql_helpers import has_autopay
from utils.sql_helpers import remove_autopay
from utils.sql_helpers import turn_off_autopay_allow
from common.models import analytics_event
from common.models.messages import ConversionEvent

cancel_subscription_router = Router()


# Отмена автоплатежа
@cancel_subscription_router.message(F.text.startswith("/cancelautopay"))
@log_function_name
@send_typing_action
async def __on_cancel_autopay(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    event = analytics_event.CancelAutopayClicked()
    db_user = await send_analytics_event(session_maker, message.from_user.id, event)

    await send_conversion_event(
        config=config,
        redis_message_broker=redis_message_broker,
        event=ConversionEvent.CANCEL_AUTOPAY,
        database_user=db_user,
    )

    async with session_maker() as session:
        try:
            user_has_autopay = await has_autopay(
                session=session, telegram_id=message.from_user.id
            )

            if not user_has_autopay:
                return await message.answer(text=ts.get("ru", "NO_AUTOPAY_TO_CANCEL"))

            return await message.answer(
                text=ts.get("ru", "CANCEL_AUTOPAY_OBJECTION"),
                reply_markup=markups.CANCEL_AUTOPAY_INLINE_KEYBOARD.as_markup(),
            )
        except TelegramForbiddenError:
            raise
        except Exception as e:
            logging.exception(
                f"{inspect.currentframe().f_code.co_name} querying database error: {e}"
            )


# Отказ от отмены автоплатежа
@cancel_subscription_router.callback_query(
    F.data.startswith(ts.get("ru", "SAVE_RECURRENT_PAYMENT_BUTTON"))
)
@log_function_name
@send_typing_action
async def __on_cancel_autopay_reject_button_clicked(
    query: CallbackQuery,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    event = analytics_event.KeepAutopayClicked()
    db_user = await send_analytics_event(session_maker, query.from_user.id, event)

    await send_conversion_event(
        config=config,
        redis_message_broker=redis_message_broker,
        event=ConversionEvent.KEEP_AUTOPAY,
        database_user=db_user,
    )

    return await query.message.answer(text=ts.get("ru", "REJECT_CANCEL_AUTOPAY_THANKS"))


# Подтверждение отмены автоплатежа
@cancel_subscription_router.callback_query(
    F.data.startswith(ts.get("ru", "CANCEL_RECURRENT_PAYMENT_BUTTON"))
)
@log_function_name
@send_typing_action
async def __on_cancel_autopay_agree_button_clicked(
    query: CallbackQuery,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=query.from_user)

    try:
        event = analytics_event.ConfirmCancelAutopayClicked()
        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CONFIRM_CANCEL_AUTOPAY,
                database_user=db_user,
            )

            await remove_autopay(session, query.from_user.id)
            await turn_off_autopay_allow(session, query.from_user.id)
            return await query.message.answer(text=ts.get("ru", "AUTOPAY_CANCELED"))

    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))
