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
from utils.config import Config
from utils.encrypt_happ_url import encrypt_happ_url, encrypt_happ_url_legacy
from utils.redis_message_broker import RedisMessageBroker
from utils.translator import translator as ts
from utils.sql_helpers import tx
from utils.sql_helpers import add_event_log
from utils.sql_helpers import save_user_in_db
from utils.sql_helpers import update_user_telegram_username
from utils.sql_helpers import get_user_by_telegram_id
from utils.sql_helpers import add_user_to_traffic_progress
from common.rwms_client import RwmsClient
from common.models import analytics_event
from common.models.messages import ConversionEvent

install_router = Router()


def _custom_json_subscription_url(subscription_url: str, client: str | None = None) -> str:
    url = subscription_url + "/custom-json"
    if client:
        return f"{url}?client={client}"
    return url


def _happ_encrypted_deep_link(subscription_url: str) -> str:
    subscription_url = _custom_json_subscription_url(subscription_url)
    happ_url = connect_urls.happ_url()
    try:
        return happ_url.replace("add/", "crypt/") + encrypt_happ_url_legacy(
            subscription_url
        )
    except ValueError:
        logging.warning(
            "Happ subscription URL is too long for legacy crypt; using crypt3"
        )
        return happ_url.replace("add/", "crypt3/") + encrypt_happ_url(
            subscription_url
        )


def _incy_plain_deep_link(subscription_url: str) -> str:
    return connect_urls.incy_import_url() + _custom_json_subscription_url(
        subscription_url,
        client="incy",
    )


def _redirect_deep_link(config: Config, deep_link: str) -> str:
    return config.redirect_url + quote(deep_link)


async def _get_or_restore_user_for_install(
    query: CallbackQuery,
    config: Config,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    telegram_id = query.from_user.id
    username = str(telegram_id)

    async with tx(session_maker) as session:
        db_user = await get_user_by_telegram_id(session, telegram_id)
        if db_user is not None:
            await update_user_telegram_username(
                session=session,
                telegram_id=telegram_id,
                telegram_username=query.from_user.username,
                bot_instance=config.bot_instance_id,
            )

    rwms_username = db_user.username if db_user is not None else username
    rw_user = await rwms_client.get_user_by_username(username=rwms_username)
    if rw_user is None and rwms_username != username:
        rw_user = await rwms_client.get_user_by_username(username=username)

    if rw_user is None:
        logging.error(f"User {rwms_username} not found")
        return None, None

    if db_user is not None:
        return db_user, rw_user

    logging.warning(
        f"User {telegram_id} has RWMS subscription but is missing in DB; restoring"
    )

    expire_at = rw_user.expire_at.ToDatetime() if rw_user.HasField("expire_at") else None

    async with tx(session_maker) as session:
        db_user = await save_user_in_db(
            session=session,
            username=username,
            referrer_id=None,
            telegram_id=telegram_id,
            expire_at=expire_at,
            telegram_username=query.from_user.username,
            bot_instance=config.bot_instance_id,
        )
        await add_user_to_traffic_progress(session=session, telegram_id=telegram_id)

    return db_user, rw_user


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
        db_user, rw_user = await _get_or_restore_user_for_install(
            query, config, rwms_client, session_maker
        )

        if db_user is None or rw_user is None:
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        async with tx(session_maker) as session:
            await add_event_log(session=session, event=event, username=db_user.username)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_ANDROID,
            database_user=db_user,
        )

        happ_url = _happ_encrypted_deep_link(rw_user.subscription_url)
        incy_url = _incy_plain_deep_link(rw_user.subscription_url)

        markup = markups.create_apps_connect_keyboard(
            happ_url=_redirect_deep_link(config, happ_url),
            incy_url=_redirect_deep_link(config, incy_url),
        )

        await query.message.answer(
            text=ts.get(
                "ru",
                "INSTALL_ON_ANDROID_INSTRUCTION",
                happ_url,
                _custom_json_subscription_url(rw_user.subscription_url),
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
        db_user, rw_user = await _get_or_restore_user_for_install(
            query, config, rwms_client, session_maker
        )

        if db_user is None or rw_user is None:
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        async with tx(session_maker) as session:
            await add_event_log(session=session, event=event, username=db_user.username)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_WINDOWS,
            database_user=db_user,
        )

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
        db_user, rw_user = await _get_or_restore_user_for_install(
            query, config, rwms_client, session_maker
        )

        if db_user is None or rw_user is None:
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        async with tx(session_maker) as session:
            await add_event_log(session=session, event=event, username=db_user.username)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_IOS,
            database_user=db_user,
        )

        happ_url = _happ_encrypted_deep_link(rw_user.subscription_url)
        incy_url = _incy_plain_deep_link(rw_user.subscription_url)

        markup = markups.create_apps_connect_keyboard(
            happ_url=_redirect_deep_link(config, happ_url),
            incy_url=_redirect_deep_link(config, incy_url)
        )

        await query.message.answer(
            text=ts.get(
                "ru",
                "INSTALL_ON_APPLE_INSTRUCTION",
                happ_url,
                _custom_json_subscription_url(rw_user.subscription_url),
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
        db_user, rw_user = await _get_or_restore_user_for_install(
            query, config, rwms_client, session_maker
        )

        if db_user is None or rw_user is None:
            return await query.message.answer(ts.get("ru", "SOMETHING_WRONG"))

        async with tx(session_maker) as session:
            await add_event_log(session=session, event=event, username=db_user.username)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_ON_MACOS,
            database_user=db_user,
        )

        happ_url = _happ_encrypted_deep_link(rw_user.subscription_url)
        incy_url = _incy_plain_deep_link(rw_user.subscription_url)

        markup = markups.create_apps_connect_keyboard(
            happ_url=_redirect_deep_link(config, happ_url),
            incy_url=_redirect_deep_link(config, incy_url)
        )

        await query.message.answer(
            text=ts.get(
                "ru",
                "INSTALL_ON_APPLE_INSTRUCTION",
                happ_url,
                _custom_json_subscription_url(rw_user.subscription_url),
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
