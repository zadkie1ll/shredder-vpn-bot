import logging
import inspect
import sqlalchemy
from urllib.parse import quote

from aiogram import Bot, Router, F
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramForbiddenError

import handlers.markups as markups
import utils.connect_urls as connect_urls
from .misc import log_function_name
from .misc import send_typing_action
from .misc import send_conversion_event
from .misc import get_log_username
from .misc import send_analytics_event
from utils.config import Config
from utils.encrypt_happ_url import encrypt_happ_url
from utils.redis_message_broker import RedisMessageBroker
from utils.translator import translator as ts
from utils.sql_helpers import tx
from utils.sql_helpers import add_event_log
from utils.sql_helpers import get_user_by_telegram_id
from common.rwms_client import RwmsClient
from common.models import analytics_event
from common.models.messages import ConversionEvent

install_router = Router()


# Кнопка "Установить на Android"
@install_router.callback_query(F.data.startswith(ts.get("ru", "ANDROID_BUTTON")))
@log_function_name
@send_typing_action
async def __install_on_android_button_clicked(
    query: CallbackQuery,
    config: Config,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=query.from_user)

    try:
        event = analytics_event.InstallOnAndroidClicked()
        db_user = await send_analytics_event(session_maker, query.from_user.id, event)

        if db_user is None:
            logging.error(f"User {query.from_user.id} not found in database")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_ANDROID,
            database_user=db_user,
        )

        rw_user = await rwms_client.get_user_by_username(username=db_user.username)

        if rw_user is None:
            logging.error(f"User {db_user.username} not found")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        encrypted_happ_url = f"happ://crypt3/{encrypt_happ_url(rw_user.subscription_url + "/custom-json")}"
        connect_url = config.redirect_url + quote(encrypted_happ_url)

        markup = markups.create_one_click_connect_keyboard(connect_url)

        await query.message.answer(
            text=ts.get("ru", "INSTALL_ON_ANDROID_INSTRUCTION", encrypted_happ_url),
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Установить на Windows"
@install_router.callback_query(F.data.startswith(ts.get("ru", "WINDOWS_BUTTON")))
@log_function_name
@send_typing_action
async def __install_on_windows_button_clicked(
    query: CallbackQuery,
    config: Config,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=query.from_user)

    try:
        event = analytics_event.InstallOnWindowsClicked()
        db_user = await send_analytics_event(session_maker, query.from_user.id, event)

        if db_user is None:
            logging.error(f"User {query.from_user.id} not found in database")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_WINDOWS,
            database_user=db_user,
        )

        rw_user = await rwms_client.get_user_by_username(username=db_user.username)

        if rw_user is None:
            logging.error(f"User {db_user.username} not found")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        connect_url = config.redirect_url + quote(
            connect_urls.flclash_url() + rw_user.subscription_url
        )
        markup = markups.create_one_click_connect_keyboard(connect_url)

        await query.message.answer(
            text=ts.get(
                "ru", "INSTALL_ON_WINDOWS_INSTRUCTION", rw_user.subscription_url
            ),
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Установить на iOS"
@install_router.callback_query(F.data.startswith(ts.get("ru", "IOS_BUTTON")))
@log_function_name
@send_typing_action
async def __install_on_ios_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=query.from_user)

    try:
        event = analytics_event.InstallOnIosClicked()
        db_user = await send_analytics_event(session_maker, query.from_user.id, event)

        if db_user is None:
            logging.error(f"User {query.from_user.id} not found in database")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_IOS,
            database_user=db_user,
        )

        rw_user = await rwms_client.get_user_by_username(username=db_user.username)

        if rw_user is None:
            logging.error(f"User {db_user.username} not found")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        encrypted_happ_url = f"happ://crypt3/{encrypt_happ_url(rw_user.subscription_url + "/custom-json")}"
        connect_url = config.redirect_url + quote(encrypted_happ_url)

        markup = markups.create_one_click_connect_keyboard(connect_url)

        await query.message.answer(
            text=ts.get("ru", "INSTALL_ON_APPLE_INSTRUCTION", encrypted_happ_url),
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Установить на macOS"
@install_router.callback_query(F.data.startswith(ts.get("ru", "MACOS_BUTTON")))
@log_function_name
@send_typing_action
async def __install_on_macos_button_clicked(
    query: CallbackQuery,
    config: Config,
    bot: Bot,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=query.from_user)

    try:
        event = analytics_event.InstallOnMacosClicked()
        db_user = await send_analytics_event(session_maker, query.from_user.id, event)

        if db_user is None:
            logging.error(f"User {query.from_user.id} not found in database")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_MACOS,
            database_user=db_user,
        )

        rw_user = await rwms_client.get_user_by_username(username=db_user.username)

        if rw_user is None:
            logging.error(f"User {query.from_user.id} not found")
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        encrypted_happ_url = f"happ://crypt3/{encrypt_happ_url(rw_user.subscription_url + "/custom-json")}"
        connect_url = config.redirect_url + quote(encrypted_happ_url)

        markup = markups.create_one_click_connect_keyboard(connect_url)

        await query.message.answer(
            text=ts.get("ru", "INSTALL_ON_APPLE_INSTRUCTION", encrypted_happ_url),
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))
