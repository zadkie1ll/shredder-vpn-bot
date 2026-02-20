import qrcode
import logging
import inspect
import sqlalchemy
from datetime import timedelta

from io import BytesIO
from urllib.parse import quote
from aiogram import F
from aiogram import Bot
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.filters import CommandObject
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from html import escape as html_escape

import handlers.markups as markups
from .misc import status_to_str
from .misc import ymid_from_args
from .misc import get_log_username
from .misc import log_function_name
from .misc import send_typing_action
from .misc import send_analytics_event
from .misc import send_analytics_event_with_session
from .misc import send_conversion_event
from .misc import traffic_source_from_args
from .misc import referrer_username_from_args
from .misc import data_limit_reset_strategy_to_str
from utils.config import Config
from utils.encrypt_happ_url import encrypt_happ_url
from utils.redis_message_broker import RedisMessageBroker
from utils.rwms_helpers import create_user
from utils.rwms_helpers import update_user
from utils.translator import translator as ts
from utils.sql_helpers import (
    get_number_of_invited_referrals,
    get_referral_bonuses_for_user,
    tx,
)
from utils.sql_helpers import add_event_log
from utils.sql_helpers import save_user_in_db
from utils.sql_helpers import update_user_ymid
from utils.sql_helpers import get_user_by_username
from utils.sql_helpers import get_user_by_telegram_id
from utils.sql_helpers import add_user_to_traffic_progress
from utils.sql_helpers import has_payment_for_user_by_tg_id
from utils.sql_helpers import get_last_traffic_source_by_telegram_id
from common.models import analytics_event
from common.rwms_client import RwmsClient
from common.models.messages import ConversionEvent

menu_router = Router()


def __get_welcome_message(
    newly_created_user: bool, has_referral_bonus: bool, first_name: str, config: Config
) -> str:
    escaped_first_name = html_escape(first_name)

    welcome_msg = ts.get(
        "ru",
        (
            "WELCOME_MESSAGE_REFERRAL"
            if has_referral_bonus and newly_created_user
            else (
                "WELCOME_MESSAGE_TRIAL_USER_CREATED"
                if newly_created_user
                else "WELCOME_MESSAGE"
            )
        ),
        escaped_first_name,
        (
            config.referral_bonus_days
            if has_referral_bonus and newly_created_user
            else config.trial_period_days
        ),
    )
    return welcome_msg


# /start
@menu_router.message(CommandStart())
@log_function_name
@send_typing_action
async def __main_menu_button_clicked(
    message: Message,
    command: CommandObject,
    config: Config,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        if message.from_user.is_bot:
            logging.info(f"ignore start command from bot {message.from_user.id}")
            return

        log_user = get_log_username(user=message.from_user)
        logging.debug(f"start command data {message.model_dump_json()}")

        ymid = ymid_from_args(command.args)
        traffic_source = traffic_source_from_args(command.args)
        referrer_username = referrer_username_from_args(command.args)

        #
        # Здесь надо запросить юзера из БД и взять его username по которому найти подписку в remnawave
        # 1. Если пользователя в бд нет, а при создании подписки для него подписка есть, нужно вытащить инфу из подписки.
        # 2. Если пользователь есть, а подписки нет, создать подписку из юзера взятого из БД
        #

        referrer = None
        if referrer_username is not None:
            async with tx(session_maker) as session:
                referrer = await get_user_by_username(session, referrer_username)

        if referrer:
            logging.info(f"referrer {referrer.username} found for user {log_user}")

        newly_created_user = False
        username = str(message.from_user.id)

        rw_user = await rwms_client.get_user_by_username(username=username)

        db_user = None

        if rw_user is None:
            logging.info(f"creating subscription for new user {log_user}")

            rw_user = await create_user(
                rwms_client=rwms_client,
                username=username,
                message=message,
                config=config,
                from_referrer=referrer is not None,
            )

            if rw_user is None:
                logging.info(f"creating subscription for {log_user} was failed")
                # написать тут сообщение пользователю о том, что не получилось создать подписку
                return await message.answer(ts.get("ru", "SOMETHING_WRONG"))
            else:
                logging.info(f"subscription for user {log_user} created")
                async with tx(session_maker) as session:
                    expire_at = None

                    if rw_user.HasField("expire_at"):
                        expire_at = rw_user.expire_at.ToDatetime()

                    # создаем пользователя в БД с реферером если он есть
                    db_user = await save_user_in_db(
                        session=session,
                        username=str(message.from_user.id),
                        referrer_id=referrer.id if referrer else None,
                        telegram_id=message.from_user.id,
                        expire_at=expire_at,
                    )

                    await add_user_to_traffic_progress(
                        session=session, telegram_id=message.from_user.id
                    )

                    event = analytics_event.SubscriptionCreated(
                        traffic_source=traffic_source
                    )
                    await add_event_log(
                        session=session, event=event, username=db_user.username
                    )
                newly_created_user = True
                logging.info(f"subscription for user {log_user} created successfully")
        else:
            async with tx(session_maker) as session:
                found_event, prev_traffic_source = (
                    await get_last_traffic_source_by_telegram_id(
                        session, message.from_user.id
                    )
                )

                if found_event and traffic_source != prev_traffic_source:
                    logging.info(
                        f"for user {message.from_user.id} detected change of traffic source, "
                        f"previous {"Direct" if prev_traffic_source is None else prev_traffic_source} "
                        f"new {"Direct" if traffic_source is None else traffic_source}"
                    )
                    event = analytics_event.TrafficSourceChanged(
                        traffic_source=traffic_source
                    )
                    await send_analytics_event_with_session(
                        session, message.from_user.id, event
                    )

        if ymid is not None:
            async with tx(session_maker) as session:
                await update_user_ymid(
                    session=session,
                    telegram_id=message.from_user.id,
                    ymid=ymid,
                )
                logging.debug(f"update ymid to {ymid} for user {message.from_user.id}")

        markup = markups.MAIN_MENU_REPLY_KEYBOARD.as_markup(resize_keyboard=True)

        await message.answer(
            __get_welcome_message(
                newly_created_user,
                referrer is not None,
                message.from_user.first_name,
                config,
            ),
            reply_markup=markup,
        )

        select_device_markup = markups.SELECT_YOUR_DEVICE_INLINE_KEYBOARD.as_markup()

        await message.answer(
            ts.get("ru", "SELECT_YOUR_DEVICE"), reply_markup=select_device_markup
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Установить VPN"
@menu_router.message(F.text.startswith(ts.get("ru", "INSTALL_VPN_BUTTON")))
@log_function_name
@send_typing_action
async def __install_vpn_button_clicked(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        log_user = get_log_username(user=message.from_user)

        event = analytics_event.InstallVpnClicked()
        db_user = await send_analytics_event(session_maker, message.from_user.id, event)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.INSTALL_VPN,
            database_user=db_user,
        )

        select_device_markup = markups.SELECT_YOUR_DEVICE_INLINE_KEYBOARD.as_markup()
        await message.answer(
            ts.get("ru", "SELECT_YOUR_DEVICE"), reply_markup=select_device_markup
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Тарифы"
@menu_router.message(F.text.startswith(ts.get("ru", "TARIFFS_BUTTON")))
@log_function_name
@send_typing_action
async def __tariffs_button_clicked(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    log_user = get_log_username(user=message.from_user)

    try:
        event = analytics_event.ShowTariffsClicked()
        db_user = await send_analytics_event(session_maker, message.from_user.id, event)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.SHOW_TARIFFS,
            database_user=db_user,
        )

        has_succeeded_payment = False

        async with session_maker() as session:
            try:
                has_succeeded_payment = await has_payment_for_user_by_tg_id(
                    session=session, telegram_id=message.from_user.id
                )

            except Exception as e:
                logging.error(f"querying database error: {e}")
                has_succeeded_payment = False

        if not has_succeeded_payment:
            return await message.answer(
                ts.get("ru", "SELECT_TARIFF"),
                reply_markup=markups.PROMO_SELECT_TARIFF_INLINE_KEYBOARD.as_markup(),
            )

        return await message.answer(
            ts.get("ru", "SELECT_TARIFF"),
            reply_markup=markups.SELECT_TARIFF_INLINE_KEYBOARD.as_markup(),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await message.answer(ts.get("ru", "SELECT_TARIFF"))


# Кнопка "Мой профиль"
@menu_router.message(F.text.startswith(ts.get("ru", "MY_PROFILE_BUTTON")))
@log_function_name
@send_typing_action
async def __my_profile_button_clicked(
    message: Message,
    config: Config,
    rwms_client: RwmsClient,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        log_user = get_log_username(user=message.from_user)

        event = analytics_event.ShowProfileClicked()
        db_user = await send_analytics_event(session_maker, message.from_user.id, event)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.SHOW_PROFILE,
            database_user=db_user,
        )

        user = await rwms_client.get_user_by_username(
            username=str(message.from_user.id)
        )

        if user is None:
            logging.error(f"User {message.chat.id} not found")
            return await message.answer(ts.get("ru", "SOMETHING_WRONG"))

        subscription_expire_timestamp = (
            user.expire_at.ToDatetime().strftime("%Y-%m-%d %H:%M:%S")
            if user.HasField("expire_at")
            else "♾️"
        )

        encrypted_happ_url = (
            f"happ://crypt3/{encrypt_happ_url(user.subscription_url + "/custom-json")}"
        )

        if user.HasField("traffic_limit_bytes") and user.traffic_limit_bytes != 0:
            await message.answer(
                ts.get(
                    "ru",
                    "MY_PROFILE_TRAFFIC_LIMIT",
                    encrypted_happ_url,
                    user.subscription_url,
                    status_to_str(user.status),
                    user.lifetime_used_traffic_bytes / (1024**3),
                    user.traffic_limit_bytes / (1024**3),
                    data_limit_reset_strategy_to_str(user.traffic_limit_strategy),
                    subscription_expire_timestamp,
                ),
                disable_web_page_preview=True,
                reply_markup=markups.MY_PROFILE_INLINE_KEYBOARD.as_markup(),
            )
        else:
            await message.answer(
                ts.get(
                    "ru",
                    "MY_PROFILE",
                    encrypted_happ_url,
                    user.subscription_url,
                    status_to_str(user.status),
                    user.lifetime_used_traffic_bytes / (1024**3),
                    subscription_expire_timestamp,
                ),
                disable_web_page_preview=True,
                reply_markup=markups.MY_PROFILE_INLINE_KEYBOARD.as_markup(),
            )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await message.answer(ts.get("ru", "SOMETHING_WRONG"))


# Кнопка "Вопросы"
@menu_router.message(F.text.startswith(ts.get("ru", "QUESTIONS_BUTTON")))
@log_function_name
@send_typing_action
async def __questions_button_clicked(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
    redis_message_broker: RedisMessageBroker,
):
    try:
        log_user = get_log_username(user=message.from_user)

        event = analytics_event.ShowQuestionsClicked()
        db_user = await send_analytics_event(session_maker, message.from_user.id, event)

        await send_conversion_event(
            config=config,
            redis_message_broker=redis_message_broker,
            event=ConversionEvent.SHOW_QUESTIONS,
            database_user=db_user,
        )

        await message.answer(
            ts.get("ru", "QUESTIONS"),
            reply_markup=markups.QUESTIONS_INLINE_KEYBOARD.as_markup(),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for {log_user}: {e}"
        )
        await message.answer(
            ts.get("ru", "QUESTIONS"),
            reply_markup=markups.QUESTIONS_INLINE_KEYBOARD.as_markup(),
        )


from aiogram.types import BufferedInputFile


# Кнопка "Пригласить друга"
@menu_router.message(F.text.startswith(ts.get("ru", "INVITE_FRIEND_BUTTON")))
@log_function_name
@send_typing_action
async def __invite_friend_button_clicked(
    message: Message,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        async with tx(session_maker) as session:
            db_user = await get_user_by_telegram_id(session, message.from_user.id)

            if db_user is None:
                logging.error(f"User {message.chat.id} not found in database")
                return await message.answer(ts.get("ru", "SOMETHING_WRONG"))

            invited_count = await get_number_of_invited_referrals(
                session, db_user.username
            )
            referral_bonuses = await get_referral_bonuses_for_user(
                session, db_user.username
            )

        referral_url = f"https://t.me/monkeyislandvpnbot?start=a{db_user.username}"

        img = qrcode.make(referral_url)

        bio = BytesIO()
        bio.name = "qrcode.png"
        img.save(bio, "PNG")
        bio.seek(0)

        encoded_share_text = quote(
            ts.get(
                "ru",
                "SHARE_REFERRAL_TEXT",
                config.referral_bonus_days,
                config.trial_period_days,
                referral_url,
            )
        )
        share_url = f"https://t.me/share/url?url={encoded_share_text}"

        builder = InlineKeyboardBuilder()
        builder.button(
            text="Поделиться ссылкой",
            url=share_url,
            style="success",
            icon_custom_emoji_id="5283158435230147109",
        )

        builder.adjust(1)

        await message.answer_photo(
            photo=BufferedInputFile(bio.read(), filename="qrcode.png"),
            caption=ts.get(
                "ru",
                "REFERRAL_PROGRAM",
                config.referrer_bonus_days,
                config.referral_bonus_days,
                config.trial_period_days,
                invited_count,
                referral_bonuses,
                referral_url,
            ),
            reply_markup=builder.as_markup(),
        )
    except TelegramForbiddenError:
        raise
    except Exception as e:
        logging.exception(
            f"{inspect.currentframe().f_code.co_name} error for user {message.chat.id}: {e}"
        )
        await message.answer(
            ts.get("ru", "SOMETHING_WRONG"),
        )
