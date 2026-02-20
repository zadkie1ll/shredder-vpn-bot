import logging
import sqlalchemy

from aiogram import F
from aiogram import Bot
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramForbiddenError

import utils.payments as payments
import handlers.buttons as buttons
from .misc import log_function_name
from .misc import send_typing_action
from .misc import send_conversion_event
from .misc import send_analytics_event_with_session
from utils.config import Config
from utils.redis_message_broker import RedisMessageBroker
from utils.translator import translator as ts
from utils.sql_helpers import tx
from utils.sql_helpers import turn_on_autopay_allow
from common.models import analytics_event
from common.models.messages import ConversionEvent
from common.models.tariff import TrialPromotionTariff
from common.models.tariff import OneDayTariff
from common.models.tariff import OneMonthTariff
from common.models.tariff import ThreeMonthsTariff
from common.models.tariff import SixMonthsTariff
from common.models.tariff import OneYearTariff

tariffs_router = Router()


# Выбран тариф "Попробовать за 10₽ на 3 дня"
@tariffs_router.callback_query(
    F.data.startswith(buttons.THREE_DAYS_PROMO_TARIFF_BUTTON)
)
@log_function_name
@send_typing_action
async def __three_days_promo_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    # т.к. сообщение о тарифах не пропадает, есть возможность несколько раз выставить такой инвойс
    # здесь нужно добавить защиту от этого и не выставлять инвойс повторно

    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceThreeDays()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_THREE_DAYS,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        tariff = TrialPromotionTariff()

        confirmation_url = await payments.create_payment(
            config.shop_id,
            config.secret,
            tariff,
            db_user,
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create trial promotion three days invoice error: {e}")


# Выбран тариф "1 день"
@tariffs_router.callback_query(F.data.startswith(buttons.ONE_DAY_TARIFF_BUTTON))
@log_function_name
@send_typing_action
async def __one_day_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceOneDay()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_ONE_DAY,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        tariff = OneDayTariff()

        confirmation_url = await payments.create_payment(
            config.shop_id, config.secret, tariff, db_user
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create one day invoice error: {e}")


# Выбран тариф "1 месяц"
@tariffs_router.callback_query(F.data.startswith(buttons.ONE_MONTH_TARIFF_BUTTON))
@log_function_name
@send_typing_action
async def __one_month_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceOneMonth()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_ONE_MONTH,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        tariff = OneMonthTariff()

        confirmation_url = await payments.create_payment(
            config.shop_id,
            config.secret,
            tariff,
            db_user,
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create one month invoice error: {e}")


# Выбран тариф "3 месяца"
@tariffs_router.callback_query(F.data.startswith(buttons.THREE_MONTHS_TARIFF_BUTTON))
@log_function_name
@send_typing_action
async def __three_months_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceThreeMonths()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_THREE_MONTHS,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        tariff = ThreeMonthsTariff()

        confirmation_url = await payments.create_payment(
            config.shop_id,
            config.secret,
            tariff,
            db_user,
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create three months invoice error: {e}")


# Выбран тариф "6 месяцев"
@tariffs_router.callback_query(F.data.startswith(buttons.SIX_MONTHS_TARIFF_BUTTON))
@log_function_name
@send_typing_action
async def __six_months_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceSixMonths()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_SIX_MONTHS,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        tariff = SixMonthsTariff()

        confirmation_url = await payments.create_payment(
            config.shop_id,
            config.secret,
            tariff,
            db_user,
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create six months invoice error: {e}")


# Выбран тариф "1 год"
@tariffs_router.callback_query(F.data.startswith(buttons.ONE_YEAR_TARIFF_BUTTON))
@log_function_name
@send_typing_action
async def __one_year_tariff_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        if query.from_user.id in config.banned:
            return await bot.send_message(
                chat_id=query.from_user.id,
                text="К сожалению для вас оплата более недоступна.",
            )

        event = analytics_event.CreateInvoiceOneYear()

        async with tx(session_maker) as session:
            db_user = await send_analytics_event_with_session(
                session, query.from_user.id, event
            )

            await send_conversion_event(
                config=config,
                redis_message_broker=redis_message_broker,
                event=ConversionEvent.CREATE_INVOICE_ONE_YEAR,
                database_user=db_user,
            )

            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            logging.debug(
                f"autopay allow flag set to true for {query.from_user.id} before creating invoice"
            )

        tariff = OneYearTariff()

        if db_user is None:
            logging.error(
                f"create invoice error: not found user by telegram id {query.from_user.id}"
            )
            await bot.send_message(query.from_user.id, ts.get("ru", "SOMETHING_WRONG"))

        confirmation_url = await payments.create_payment(
            config.shop_id,
            config.secret,
            tariff,
            db_user,
        )

        logging.info(
            f"an invoice for the {tariff.db_tariff_id} tariff has been created for "
            f"{query.from_user.id}, confirmation url: {confirmation_url}"
        )

        await bot.send_message(
            chat_id=query.from_user.id,
            text=ts.get("ru", "YOUR_PAYMENT", tariff.price, confirmation_url),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        await bot.send_message(
            chat_id=query.from_user.id, text=ts.get("ru", "SOMETHING_WRONG")
        )
        logging.exception(f"create one year invoice error: {e}")
