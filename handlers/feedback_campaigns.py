import logging

import sqlalchemy
from aiogram import F
from aiogram import Bot
from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.exceptions import TelegramForbiddenError

import utils.payments as payments
from filters.is_admin import IsAdmin
from repositories import feedback_campaigns as repo
from services import feedback_campaigns as feedback_service
from texts import feedback_campaigns as feedback_texts
from utils.config import Config
from utils.sql_helpers import get_user_by_telegram_id
from utils.sql_helpers import turn_on_autopay_allow
from utils.sql_helpers import tx
from common.models.tariff import str_to_tariff

feedback_campaigns_router = Router()


def parse_min_text_length(args: list[str], index: int, survey_type) -> int | None:
    if survey_type != feedback_service.FeedbackSurveyType.TEXT:
        return None
    if len(args) <= index:
        return feedback_texts.DEFAULT_MIN_TEXT_LENGTH
    try:
        value = int(args[index])
    except ValueError as exc:
        raise ValueError("min_chars must be an integer") from exc
    if value < 1:
        raise ValueError("min_chars must be positive")
    return value


@feedback_campaigns_router.message(F.text.startswith("/feedback_test"), IsAdmin())
async def on_feedback_test(
    message: Message,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if len(args) < 3:
        await message.answer(
            "Формат: /feedback_test <telegram_id> <buttons|text> <month,sixmonths,year> [min_chars]"
        )
        return

    try:
        telegram_id = int(args[0])
        survey_type = feedback_service.parse_survey_type(args[1])
        reward_options = feedback_service.parse_reward_options(args[2])
        min_text_length = parse_min_text_length(args, 3, survey_type)

        result = await feedback_service.start_feedback_test(
            bot=bot,
            session_maker=session_maker,
            admin_telegram_id=message.from_user.id,
            telegram_id=telegram_id,
            survey_type=survey_type,
            reward_options=reward_options,
            min_text_length=min_text_length,
        )
        await message.answer(
            f"Тестовая feedback-рассылка создана.\n"
            f"run_id: <code>{result.run_id}</code>\n"
            f"Отправлено: {result.sent_count}, ошибок: {result.failed_count}"
        )
    except ValueError as exc:
        await message.answer(f"Ошибка: {exc}")
    except Exception as exc:
        logging.exception("feedback_test failed: %s", exc)
        await message.answer("Не получилось запустить тестовую feedback-рассылку.")


@feedback_campaigns_router.message(F.text.startswith("/feedback_send"), IsAdmin())
async def on_feedback_send(
    message: Message,
    bot: Bot,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if len(args) < 3:
        await message.answer(
            "Формат: /feedback_send <count> <buttons|text> <month,sixmonths,year> [min_chars]"
        )
        return

    try:
        limit = int(args[0])
        if limit < 1:
            raise ValueError("count must be positive")
        survey_type = feedback_service.parse_survey_type(args[1])
        reward_options = feedback_service.parse_reward_options(args[2])
        min_text_length = parse_min_text_length(args, 3, survey_type)

        await message.answer("Запускаю feedback-рассылку...")
        result = await feedback_service.start_feedback_send(
            bot=bot,
            session_maker=session_maker,
            admin_telegram_id=message.from_user.id,
            limit=limit,
            survey_type=survey_type,
            reward_options=reward_options,
            min_text_length=min_text_length,
        )
        await message.answer(
            f"Feedback-рассылка завершена.\n"
            f"run_id: <code>{result.run_id}</code>\n"
            f"Выбрано: {result.selected_count}, отправлено: {result.sent_count}, "
            f"ошибок: {result.failed_count}"
        )
    except ValueError as exc:
        await message.answer(f"Ошибка: {exc}")
    except Exception as exc:
        logging.exception("feedback_send failed: %s", exc)
        await message.answer("Не получилось запустить feedback-рассылку.")


@feedback_campaigns_router.callback_query(F.data.startswith("fb_answer:"))
async def on_feedback_button_answer(
    query: CallbackQuery,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        _, recipient_id, button_value = query.data.split(":")
        reward_id, reward_options = (
            await feedback_service.save_button_answer_and_issue_reward(
                session_maker=session_maker,
                telegram_id=query.from_user.id,
                recipient_id=int(recipient_id),
                button_value=int(button_value),
            )
        )
        await query.message.answer(
            feedback_texts.REWARD_ISSUED,
            reply_markup=feedback_service.build_reward_keyboard(
                reward_id, reward_options
            ),
        )
        await query.answer("Спасибо за ответ!")
    except Exception as exc:
        logging.exception("feedback button answer failed: %s", exc)
        await query.answer("Не получилось сохранить ответ", show_alert=True)


@feedback_campaigns_router.message(F.text, ~F.text.startswith("/"))
async def on_feedback_text_answer(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        reward_id, reward_options = (
            await feedback_service.save_text_answer_and_issue_reward(
                session_maker=session_maker,
                telegram_id=message.from_user.id,
                answer_text=message.text,
            )
        )
        if reward_id is None:
            return

        await message.answer(
            feedback_texts.REWARD_ISSUED,
            reply_markup=feedback_service.build_reward_keyboard(
                reward_id, reward_options
            ),
        )
    except ValueError as exc:
        await message.answer(str(exc))
    except TelegramForbiddenError:
        raise
    except Exception as exc:
        logging.exception("feedback text answer failed: %s", exc)
        await message.answer("Не получилось сохранить ответ.")


@feedback_campaigns_router.callback_query(F.data.startswith("fb_reward:"))
async def on_feedback_reward_selected(
    query: CallbackQuery,
    config: Config,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        _, reward_id_raw, subscription_period = query.data.split(":")
        reward_id = int(reward_id_raw)

        async with tx(session_maker) as session:
            reward = await repo.get_reward_for_user(
                session,
                reward_id=reward_id,
                telegram_id=query.from_user.id,
            )
            if reward is None:
                await query.answer(
                    "Скидка не найдена или уже использована.", show_alert=True
                )
                return

            option = feedback_service.get_reward_option(
                reward.reward_options,
                subscription_period,
            )
            if option is None:
                await query.answer("Такого тарифа нет в этой скидке.", show_alert=True)
                return

            db_user = await get_user_by_telegram_id(session, query.from_user.id)
            await turn_on_autopay_allow(session=session, telegram_id=query.from_user.id)
            await repo.mark_reward_selected(
                session,
                reward_id=reward.id,
                subscription_period=subscription_period,
                discount_percent=option.get("discount_percent"),
                discount_amount=option.get("discount_amount"),
            )

        tariff = str_to_tariff(subscription_period)
        price = feedback_service.discounted_price(option)
        confirmation_url = await payments.create_discount_payment(
            config.shop_id,
            config.secret,
            tariff,
            price,
            db_user,
            reward.code,
        )

        await query.message.answer(
            feedback_texts.PAYMENT_WITH_DISCOUNT.format(
                period=feedback_texts.reward_button_text(option).split(" - ")[0],
                price=price,
                original_price=tariff.price,
                discount_percent=option.get("discount_percent") or 0,
                url=confirmation_url,
            ),
            disable_web_page_preview=True,
        )
        await query.answer("Ссылка на оплату готова")
    except TelegramForbiddenError:
        raise
    except Exception as exc:
        logging.exception("feedback reward selection failed: %s", exc)
        await query.answer("Не получилось создать оплату", show_alert=True)


@feedback_campaigns_router.message(F.text.startswith("/feedback_status"), IsAdmin())
async def on_feedback_status(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Формат: /feedback_status <run_id>")
        return
    try:
        run_id = int(args[0])
        async with tx(session_maker) as session:
            counts = await repo.get_run_counts(session, run_id)
        await message.answer(
            f"run_id: <code>{run_id}</code>\n"
            f"Отправлено: {counts['sent']}\n"
            f"Ответили: {counts['answered']}\n"
            f"Наград выдано: {counts['rewarded']}\n"
            f"Ошибок: {counts['failed']}"
        )
    except ValueError:
        await message.answer("run_id должен быть числом")


@feedback_campaigns_router.message(F.text.startswith("/feedback_cancel"), IsAdmin())
async def on_feedback_cancel(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Формат: /feedback_cancel <run_id>")
        return
    try:
        run_id = int(args[0])
        async with tx(session_maker) as session:
            cancelled = await repo.cancel_run(session, run_id)
        if cancelled:
            await message.answer(f"run_id <code>{run_id}</code> помечен как cancelled.")
        else:
            await message.answer("Такой run_id не найден.")
    except ValueError:
        await message.answer("run_id должен быть числом")
