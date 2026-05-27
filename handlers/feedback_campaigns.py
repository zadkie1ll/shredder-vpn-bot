import logging
from html import escape

import sqlalchemy
from aiogram import F
from aiogram import Bot
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder

import utils.payments as payments
from filters.is_admin import IsAdmin
from handlers.feedback_states import FeedbackBroadcastStates
from repositories import feedback_campaigns as repo
from services import feedback_campaigns as feedback_service
from texts import feedback_campaigns as feedback_texts
from utils.config import Config
from utils.sql_helpers import get_user_by_telegram_id
from utils.sql_helpers import turn_on_autopay_allow
from utils.sql_helpers import tx
from utils.sql_helpers import update_user_telegram_username
from common.models.tariff import str_to_tariff

feedback_campaigns_router = Router()
FEEDBACK_PREVIEW_CHUNK_SIZE = 120
FEEDBACK_RUNS_DEFAULT_LIMIT = 20
FEEDBACK_RUNS_MAX_LIMIT = 20
FEEDBACK_TEXT_PREVIEW_LIMIT = 3000


def enum_value(value) -> str:
    return getattr(value, "value", str(value))


def format_feedback_run_date(value) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def format_reward_periods(reward_options: list[dict] | None) -> str:
    if not reward_options:
        return "-"
    return ", ".join(option["subscription_period"] for option in reward_options)


def format_feedback_runs(rows: list[dict]) -> str:
    if not rows:
        return "Продовых feedback-рассылок пока нет."

    lines = ["Продовые feedback-рассылки:"]
    for row in rows:
        audience_count = row["audience_count"] or 0
        user_limit = row["user_limit"] or audience_count
        lines.extend(
            [
                "",
                f"run_id: <code>{row['run_id']}</code> | "
                f"{enum_value(row['survey_type'])} | {enum_value(row['status'])}",
                f"Дата: {format_feedback_run_date(row['created_at'])} UTC",
                f"Аудитория: {audience_count}/{user_limit} | "
                f"отправлено: {row['sent_count'] or 0} | "
                f"ответили: {row['answered_count'] or 0} | "
                f"купили: {row['paid_count'] or 0}",
                f"Скидки: {format_reward_periods(row['reward_options'])}",
            ]
        )
    return "\n".join(lines)


def build_reward_stats_lines(
    reward_options: list[dict] | None,
    reward_stats: dict[str, dict[str, int]],
) -> list[str]:
    periods = []
    for option in reward_options or []:
        period = option["subscription_period"]
        if period not in periods:
            periods.append(period)
    for period in reward_stats:
        if period not in periods:
            periods.append(period)

    if not periods:
        return ["Скидки: -"]

    lines = ["Скидки:"]
    for period in periods:
        stats = reward_stats.get(period, {})
        lines.append(
            f"{period}: выбрали {stats.get('selected', 0)}, "
            f"оплатили {stats.get('used', 0)}"
        )
    return lines


def format_buttons_results(
    *,
    run,
    campaign,
    counts: dict[str, int],
    button_stats: dict[int, int],
    reward_stats: dict[str, dict[str, int]],
) -> str:
    lines = [
        f"Feedback run <code>{run.id}</code>",
        f"Тип: {enum_value(campaign.survey_type)}",
        f"Статус: {enum_value(run.status)}",
        f"Дата: {format_feedback_run_date(run.created_at)} UTC",
        "",
        f"Отправлено: {counts['sent']}",
        f"Ответили: {counts['answered']}",
        f"Наград выдано: {counts['rewarded']}",
        f"Ошибок: {counts['failed']}",
        "",
        "Ответы:",
    ]
    for option in campaign.button_options or []:
        value = option["value"]
        lines.append(f"{option['text']}: {button_stats.get(value, 0)}")
    if not campaign.button_options:
        lines.append("-")

    lines.append("")
    lines.extend(build_reward_stats_lines(campaign.reward_options, reward_stats))
    return "\n".join(lines)


def trim_feedback_text(value: str | None) -> str:
    if not value:
        return "-"
    if len(value) <= FEEDBACK_TEXT_PREVIEW_LIMIT:
        return value
    return value[:FEEDBACK_TEXT_PREVIEW_LIMIT] + "\n... текст обрезан"


def format_text_results_page(
    *,
    run,
    campaign,
    counts: dict[str, int],
    reward_stats: dict[str, dict[str, int]],
    page: int,
    total: int,
    answer: dict | None,
) -> str:
    lines = [
        f"Feedback run <code>{run.id}</code>",
        f"Тип: {enum_value(campaign.survey_type)}",
        f"Статус: {enum_value(run.status)}",
        f"Дата: {format_feedback_run_date(run.created_at)} UTC",
        "",
        f"Отправлено: {counts['sent']}",
        f"Ответили: {counts['answered']}",
        f"Наград выдано: {counts['rewarded']}",
        f"Ошибок: {counts['failed']}",
        "",
    ]
    lines.extend(build_reward_stats_lines(campaign.reward_options, reward_stats))
    lines.append("")

    if total == 0 or answer is None:
        lines.append("Текстовых ответов пока нет.")
        return "\n".join(lines)

    username = answer.get("telegram_username")
    username_text = f"@{escape(username)}" if username else "@unknown"
    text_value = escape(trim_feedback_text(answer.get("text_value")))
    lines.extend(
        [
            f"Ответ {page + 1} / {total}",
            "",
            f"TG: {username_text}",
            f"TG ID: <code>{answer['telegram_id']}</code>",
            f"User ID: <code>{answer['user_id']}</code>",
            f"Дата ответа: {format_feedback_run_date(answer['created_at'])} UTC",
            "",
            "Фидбек:",
            text_value,
        ]
    )
    return "\n".join(lines)


def build_text_results_keyboard(run_id: int, page: int, total: int):
    if total <= 1:
        return None

    prev_page = max(page - 1, 0)
    next_page = min(page + 1, total - 1)
    builder = InlineKeyboardBuilder()
    builder.button(
        text="← Назад",
        callback_data=f"fb_text_results:{run_id}:{prev_page}",
    )
    builder.button(
        text="Далее →",
        callback_data=f"fb_text_results:{run_id}:{next_page}",
    )
    builder.adjust(2)
    return builder.as_markup()


async def build_feedback_results_message(
    session,
    run_id: int,
    page: int = 0,
) -> tuple[str, object | None] | None:
    context = await repo.get_run_with_campaign(session, run_id)
    if context is None:
        return None

    run, campaign = context
    counts = await repo.get_run_counts(session, run_id)
    reward_stats = await repo.get_reward_period_stats(session, run_id)

    if campaign.survey_type == feedback_service.FeedbackSurveyType.BUTTONS:
        button_stats = await repo.get_button_answer_stats(session, run_id)
        return (
            format_buttons_results(
                run=run,
                campaign=campaign,
                counts=counts,
                button_stats=button_stats,
                reward_stats=reward_stats,
            ),
            None,
        )

    total = await repo.get_text_answer_count(session, run_id)
    if total:
        page = max(0, min(page, total - 1))
    else:
        page = 0
    answer = await repo.get_text_answer_page(session, run_id, page) if total else None
    return (
        format_text_results_page(
            run=run,
            campaign=campaign,
            counts=counts,
            reward_stats=reward_stats,
            page=page,
            total=total,
            answer=answer,
        ),
        build_text_results_keyboard(run_id, page, total),
    )


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


async def send_feedback_audience_preview(
    message: Message,
    telegram_ids: list[int],
) -> None:
    if not telegram_ids:
        await message.answer("В аудиторию feedback-рассылки никто не попал.")
        return

    for start in range(0, len(telegram_ids), FEEDBACK_PREVIEW_CHUNK_SIZE):
        chunk = telegram_ids[start : start + FEEDBACK_PREVIEW_CHUNK_SIZE]
        lines = [
            f"{index}. <code>{telegram_id}</code>"
            for index, telegram_id in enumerate(chunk, start + 1)
        ]
        await message.answer(
            "TG ID, которые попадут в feedback-рассылку:\n" + "\n".join(lines)
        )


def build_feedback_confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить отправку", callback_data="fb_send_confirm")
    builder.button(text="Отмена", callback_data="fb_send_cancel")
    builder.adjust(1)
    return builder.as_markup()


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
    state: FSMContext,
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

        users = await feedback_service.preview_feedback_audience(
            session_maker=session_maker,
            limit=limit,
        )
        telegram_ids = [user.telegram_id for user in users]
        await send_feedback_audience_preview(message, telegram_ids)
        if not telegram_ids:
            return

        await state.set_state(FeedbackBroadcastStates.confirm)
        await state.update_data(
            telegram_ids=telegram_ids,
            survey_type=survey_type.value,
            reward_options=reward_options,
            min_text_length=min_text_length,
        )
        await message.answer(
            f"Перед отправкой проверь список выше.\n"
            f"Всего получателей: <b>{len(telegram_ids)}</b>.\n"
            f"Запустить feedback-рассылку?",
            reply_markup=build_feedback_confirm_keyboard(),
        )
    except ValueError as exc:
        await message.answer(f"Ошибка: {exc}")
    except Exception as exc:
        logging.exception("feedback_send failed: %s", exc)
        await message.answer("Не получилось запустить feedback-рассылку.")


@feedback_campaigns_router.callback_query(
    FeedbackBroadcastStates.confirm,
    F.data.in_({"fb_send_confirm", "fb_send_cancel"}),
    IsAdmin(),
)
async def on_feedback_send_confirm(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    if query.data == "fb_send_cancel":
        await state.clear()
        await query.message.edit_text("Feedback-рассылка отменена.")
        await query.answer()
        return

    data = await state.get_data()
    await state.clear()
    await query.message.edit_text("Запускаю feedback-рассылку...")

    try:
        result = await feedback_service.start_feedback_send_for_telegram_ids(
            bot=bot,
            session_maker=session_maker,
            admin_telegram_id=query.from_user.id,
            telegram_ids=data["telegram_ids"],
            survey_type=feedback_service.parse_survey_type(data["survey_type"]),
            reward_options=data["reward_options"],
            min_text_length=data.get("min_text_length"),
        )
        await query.message.answer(
            f"Feedback-рассылка завершена.\n"
            f"run_id: <code>{result.run_id}</code>\n"
            f"Выбрано: {result.selected_count}, отправлено: {result.sent_count}, "
            f"ошибок: {result.failed_count}"
        )
        await query.answer("Рассылка запущена")
    except Exception as exc:
        logging.exception("feedback_send confirm failed: %s", exc)
        await query.message.answer("Не получилось отправить feedback-рассылку.")
        await query.answer("Ошибка отправки", show_alert=True)


@feedback_campaigns_router.callback_query(F.data.startswith("fb_answer:"))
async def on_feedback_button_answer(
    query: CallbackQuery,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        async with tx(session_maker) as session:
            await update_user_telegram_username(
                session, query.from_user.id, query.from_user.username
            )
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
        async with tx(session_maker) as session:
            await update_user_telegram_username(
                session, message.from_user.id, message.from_user.username
            )
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


@feedback_campaigns_router.message(F.text.startswith("/feedback_runs"), IsAdmin())
async def on_feedback_runs(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    try:
        limit = FEEDBACK_RUNS_DEFAULT_LIMIT
        if args:
            limit = int(args[0])
        if limit < 1:
            raise ValueError("limit must be positive")
        limit = min(limit, FEEDBACK_RUNS_MAX_LIMIT)

        async with tx(session_maker) as session:
            rows = await repo.get_production_run_summaries(session, limit)
        await message.answer(format_feedback_runs(rows))
    except ValueError:
        await message.answer("Формат: /feedback_runs [limit]")
    except Exception as exc:
        logging.exception("feedback_runs failed: %s", exc)
        await message.answer("Не получилось получить список feedback-рассылок.")


@feedback_campaigns_router.message(F.text.startswith("/feedback_results"), IsAdmin())
async def on_feedback_results(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Формат: /feedback_results <run_id>")
        return

    try:
        run_id = int(args[0])
        async with tx(session_maker) as session:
            result = await build_feedback_results_message(session, run_id)
        if result is None:
            await message.answer("Такой run_id не найден.")
            return
        text, keyboard = result
        await message.answer(text, reply_markup=keyboard)
    except ValueError:
        await message.answer("run_id должен быть числом")
    except Exception as exc:
        logging.exception("feedback_results failed: %s", exc)
        await message.answer("Не получилось получить результаты feedback-рассылки.")


@feedback_campaigns_router.callback_query(
    F.data.startswith("fb_text_results:"),
    IsAdmin(),
)
async def on_feedback_text_results_page(
    query: CallbackQuery,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    try:
        _, run_id_raw, page_raw = query.data.split(":")
        run_id = int(run_id_raw)
        page = int(page_raw)
        async with tx(session_maker) as session:
            result = await build_feedback_results_message(session, run_id, page)
        if result is None:
            await query.answer("Такой run_id не найден.", show_alert=True)
            return
        text, keyboard = result
        await query.message.edit_text(text, reply_markup=keyboard)
        await query.answer()
    except Exception as exc:
        logging.exception("feedback text results page failed: %s", exc)
        await query.answer("Не получилось открыть страницу", show_alert=True)


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


@feedback_campaigns_router.message(F.text.startswith("/feedback_cleanup"), IsAdmin())
async def on_feedback_cleanup(
    message: Message,
    session_maker: sqlalchemy.ext.asyncio.async_sessionmaker,
):
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Формат: /feedback_cleanup <older_than_days>")
        return

    try:
        older_than_days = int(args[0])
        if older_than_days < 1:
            raise ValueError("older_than_days must be positive")

        cleaned_count = await feedback_service.cleanup_old_production_recipients(
            session_maker=session_maker,
            older_than_days=older_than_days,
        )
        await message.answer(
            f"Очищено получателей feedback-рассылок старше "
            f"{older_than_days} дней: <b>{cleaned_count}</b>.\n"
            f"Эти пользователи снова доступны для продовой feedback-рассылки."
        )
    except ValueError as exc:
        await message.answer(f"Ошибка: {exc}")
    except Exception as exc:
        logging.exception("feedback cleanup failed: %s", exc)
        await message.answer("Не получилось очистить старых получателей.")
