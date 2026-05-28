import asyncio
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import async_sessionmaker

from common.models.db import FeedbackAnswerType
from common.models.db import FeedbackCampaign
from common.models.db import FeedbackCampaignRecipient
from common.models.db import FeedbackRunMode
from common.models.db import FeedbackSurveyType
from common.models.db import User
from common.models.tariff import str_to_tariff
from repositories import feedback_campaigns as repo
from texts import feedback_campaigns as texts
from utils.sql_helpers import tx

ALLOWED_REWARD_PERIODS = {"month", "sixmonths", "year"}
FREE_DAYS_REWARD = "days"
MIN_FREE_REWARD_DAYS = 1
MAX_FREE_REWARD_DAYS = 365


@dataclass
class FeedbackSendResult:
    run_id: int
    selected_count: int
    sent_count: int
    failed_count: int


@dataclass
class FeedbackButtonAnswerResult:
    reward_id: int | None
    reward_options: list[dict] | None
    requires_text: bool = False
    show_connection_support: bool = False


def parse_reward_options(value: str) -> list[dict]:
    reward_options = []
    seen_periods = set()
    seen_free_days = set()
    for raw_option in value.split(","):
        option = raw_option.strip().lower()
        if not option:
            continue

        raw_period, separator, raw_discount_percent = option.partition(":")
        period = raw_period.strip()
        if period == FREE_DAYS_REWARD:
            if not separator or not raw_discount_percent:
                raise ValueError("free days reward format: days:<count>")
            try:
                days = int(raw_discount_percent)
            except ValueError as exc:
                raise ValueError("free days reward must be an integer") from exc
            if not (MIN_FREE_REWARD_DAYS <= days <= MAX_FREE_REWARD_DAYS):
                raise ValueError(
                    "free days reward must be between "
                    f"{MIN_FREE_REWARD_DAYS} and {MAX_FREE_REWARD_DAYS}"
                )
            if days in seen_free_days:
                raise ValueError(f"duplicate free days reward: days:{days}")
            seen_free_days.add(days)
            reward_options.append(
                {
                    "reward_type": "free_days",
                    "days": days,
                }
            )
            continue

        if period not in ALLOWED_REWARD_PERIODS:
            raise ValueError("allowed rewards: month,sixmonths,year,days:<count>")
        if period in seen_periods:
            raise ValueError(f"duplicate reward period: {period}")

        discount_percent = texts.DEFAULT_DISCOUNT_PERCENT
        if separator:
            if not raw_discount_percent:
                raise ValueError(f"discount percent is required for {period}")
            try:
                discount_percent = int(raw_discount_percent)
            except ValueError as exc:
                raise ValueError("discount percent must be an integer") from exc
            if not (
                texts.MIN_DISCOUNT_PERCENT
                <= discount_percent
                <= texts.MAX_DISCOUNT_PERCENT
            ):
                raise ValueError(
                    "discount percent must be between "
                    f"{texts.MIN_DISCOUNT_PERCENT} and "
                    f"{texts.MAX_DISCOUNT_PERCENT}"
                )

        seen_periods.add(period)
        reward_options.append(
            {
                "reward_type": "discount",
                "subscription_period": period,
                "discount_percent": discount_percent,
            }
        )

    if not reward_options:
        raise ValueError("at least one reward period is required")

    return reward_options


def parse_survey_type(value: str) -> FeedbackSurveyType:
    normalized = value.strip().lower()
    if normalized == FeedbackSurveyType.BUTTONS.value:
        return FeedbackSurveyType.BUTTONS
    if normalized == FeedbackSurveyType.TEXT.value:
        return FeedbackSurveyType.TEXT
    raise ValueError("survey type must be buttons or text")


def build_survey_keyboard(
    campaign: FeedbackCampaign,
    recipient: FeedbackCampaignRecipient,
) -> InlineKeyboardMarkup | None:
    if campaign.survey_type != FeedbackSurveyType.BUTTONS:
        return None

    builder = InlineKeyboardBuilder()
    for option in campaign.button_options:
        builder.button(
            text=option["text"],
            callback_data=f"fb_answer:{recipient.id}:{option['value']}",
        )
    builder.adjust(1)
    return builder.as_markup()


def build_reward_keyboard(
    reward_id: int, reward_options: list[dict]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for option in reward_options:
        if option.get("reward_type") == "free_days":
            builder.button(
                text=texts.free_days_reward_button_text(option),
                callback_data=f"fb_reward_days:{reward_id}:{option['days']}",
            )
        else:
            builder.button(
                text=texts.reward_button_text(option),
                callback_data=f"fb_reward:{reward_id}:{option['subscription_period']}",
            )
    builder.adjust(1)
    return builder.as_markup()


def get_reward_option(
    reward_options: list[dict], subscription_period: str
) -> dict | None:
    for option in reward_options:
        if option.get("reward_type") == "free_days":
            continue
        if option["subscription_period"] == subscription_period:
            return option
    return None


def get_free_days_reward_option(reward_options: list[dict], days: int) -> dict | None:
    for option in reward_options:
        if option.get("reward_type") == "free_days" and option.get("days") == days:
            return option
    return None


def discounted_price(option: dict) -> int:
    tariff = str_to_tariff(option["subscription_period"])
    discount_percent = option.get("discount_percent") or 0
    discount_amount = option.get("discount_amount") or 0
    if discount_percent:
        return max(1, round(tariff.price * (100 - discount_percent) / 100))
    return max(1, tariff.price - discount_amount)


def build_button_options(
    *,
    ask_missing_location_text: bool,
    show_connection_support: bool,
) -> list[dict]:
    options = []
    for option in texts.SURVEY_BUTTON_OPTIONS:
        option = dict(option)
        if option["value"] == texts.OTHER_REASON_BUTTON_VALUE:
            option["requires_text"] = True
        elif option["value"] == texts.MISSING_LOCATION_BUTTON_VALUE:
            option["requires_text"] = ask_missing_location_text
        elif option["value"] == texts.CONNECTION_PROBLEM_BUTTON_VALUE:
            option["show_connection_support"] = show_connection_support
        options.append(option)
    return options


def get_button_option(campaign: FeedbackCampaign, button_value: int) -> dict | None:
    for option in campaign.button_options or []:
        if option["value"] == button_value:
            return option
    return None


def button_requires_text(campaign: FeedbackCampaign, button_value: int) -> bool:
    option = get_button_option(campaign, button_value)
    if option is not None and "requires_text" in option:
        return bool(option.get("requires_text"))
    return button_value == texts.OTHER_REASON_BUTTON_VALUE


def button_shows_connection_support(
    campaign: FeedbackCampaign,
    button_value: int,
) -> bool:
    option = get_button_option(campaign, button_value)
    if option is None:
        return False
    return bool(option.get("show_connection_support"))


async def preview_feedback_audience(
    *,
    session_maker: async_sessionmaker,
    limit: int,
) -> list[User]:
    async with tx(session_maker) as session:
        return await repo.get_feedback_audience(session, limit)


async def cleanup_old_production_recipients(
    *,
    session_maker: async_sessionmaker,
    older_than_days: int,
) -> int:
    async with tx(session_maker) as session:
        return await repo.cleanup_old_production_recipients(session, older_than_days)


async def start_feedback_test(
    *,
    bot: Bot,
    session_maker: async_sessionmaker,
    admin_telegram_id: int,
    telegram_id: int,
    survey_type: FeedbackSurveyType,
    reward_options: list[dict],
    min_text_length: int | None,
    ask_missing_location_text: bool = False,
    show_connection_support: bool = False,
) -> FeedbackSendResult:
    async with tx(session_maker) as session:
        user = await repo.get_test_audience_user(session, telegram_id)
        if user is None:
            raise ValueError(f"user {telegram_id} not found")
        campaign, run = await repo.create_campaign_with_run(
            session,
            title="Feedback test",
            survey_type=survey_type,
            min_text_length=min_text_length,
            message_text_key=survey_type.value,
            button_options=build_button_options(
                ask_missing_location_text=ask_missing_location_text,
                show_connection_support=show_connection_support,
            ),
            reward_options=reward_options,
            run_mode=FeedbackRunMode.TEST_USER,
            created_by_telegram_id=admin_telegram_id,
            test_telegram_id=telegram_id,
            user_limit=1,
        )
        recipient = await repo.create_recipient(
            session,
            campaign_id=campaign.id,
            run_id=run.id,
            user=user,
        )

    sent, failed = await send_feedback_to_recipients(
        bot=bot,
        session_maker=session_maker,
        campaign=campaign,
        recipients=[recipient],
    )

    async with tx(session_maker) as session:
        await repo.finish_run(session, run.id)

    return FeedbackSendResult(run.id, 1, sent, failed)


async def start_feedback_send(
    *,
    bot: Bot,
    session_maker: async_sessionmaker,
    admin_telegram_id: int,
    limit: int,
    survey_type: FeedbackSurveyType,
    reward_options: list[dict],
    min_text_length: int | None,
    ask_missing_location_text: bool = False,
    show_connection_support: bool = False,
) -> FeedbackSendResult:
    async with tx(session_maker) as session:
        users = await repo.get_feedback_audience(session, limit)
        campaign, run = await repo.create_campaign_with_run(
            session,
            title="Trial feedback",
            survey_type=survey_type,
            min_text_length=min_text_length,
            message_text_key=survey_type.value,
            button_options=build_button_options(
                ask_missing_location_text=ask_missing_location_text,
                show_connection_support=show_connection_support,
            ),
            reward_options=reward_options,
            run_mode=FeedbackRunMode.NEAREST_EXPIRING,
            created_by_telegram_id=admin_telegram_id,
            user_limit=limit,
        )
        recipients = []
        for user in users:
            recipients.append(
                await repo.create_recipient(
                    session,
                    campaign_id=campaign.id,
                    run_id=run.id,
                    user=user,
                )
            )

    sent, failed = await send_feedback_to_recipients(
        bot=bot,
        session_maker=session_maker,
        campaign=campaign,
        recipients=recipients,
    )

    async with tx(session_maker) as session:
        await repo.finish_run(session, run.id)

    return FeedbackSendResult(run.id, len(recipients), sent, failed)


async def start_feedback_send_for_telegram_ids(
    *,
    bot: Bot,
    session_maker: async_sessionmaker,
    admin_telegram_id: int,
    telegram_ids: list[int],
    survey_type: FeedbackSurveyType,
    reward_options: list[dict],
    min_text_length: int | None,
    ask_missing_location_text: bool = False,
    show_connection_support: bool = False,
) -> FeedbackSendResult:
    async with tx(session_maker) as session:
        users = await repo.get_users_by_telegram_ids(session, telegram_ids)
        campaign, run = await repo.create_campaign_with_run(
            session,
            title="Trial feedback",
            survey_type=survey_type,
            min_text_length=min_text_length,
            message_text_key=survey_type.value,
            button_options=build_button_options(
                ask_missing_location_text=ask_missing_location_text,
                show_connection_support=show_connection_support,
            ),
            reward_options=reward_options,
            run_mode=FeedbackRunMode.NEAREST_EXPIRING,
            created_by_telegram_id=admin_telegram_id,
            user_limit=len(telegram_ids),
        )
        recipients = []
        for user in users:
            recipients.append(
                await repo.create_recipient(
                    session,
                    campaign_id=campaign.id,
                    run_id=run.id,
                    user=user,
                )
            )

    sent, failed = await send_feedback_to_recipients(
        bot=bot,
        session_maker=session_maker,
        campaign=campaign,
        recipients=recipients,
    )

    async with tx(session_maker) as session:
        await repo.finish_run(session, run.id)

    return FeedbackSendResult(run.id, len(recipients), sent, failed)


async def send_feedback_to_recipients(
    *,
    bot: Bot,
    session_maker: async_sessionmaker,
    campaign: FeedbackCampaign,
    recipients: list[FeedbackCampaignRecipient],
) -> tuple[int, int]:
    sent = 0
    failed = 0
    for recipient in recipients:
        try:
            message = await bot.send_message(
                chat_id=recipient.telegram_id_snapshot,
                text=texts.SURVEY_MESSAGES[campaign.message_text_key],
                reply_markup=build_survey_keyboard(campaign, recipient),
                disable_web_page_preview=True,
            )
            async with tx(session_maker) as session:
                await repo.mark_recipient_sent(
                    session, recipient.id, message.message_id
                )
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            failed += 1
        except Exception as exc:
            async with tx(session_maker) as session:
                await repo.mark_recipient_failed(session, recipient.id, str(exc))
            failed += 1
        await asyncio.sleep(0.05)
    return sent, failed


async def save_button_answer_and_issue_reward(
    *,
    session_maker: async_sessionmaker,
    telegram_id: int,
    recipient_id: int,
    button_value: int,
) -> FeedbackButtonAnswerResult:
    async with tx(session_maker) as session:
        context = await repo.get_recipient_with_campaign(session, recipient_id)
        if context is None:
            raise ValueError("feedback recipient not found")
        recipient, campaign = context
        if recipient.telegram_id_snapshot != telegram_id:
            raise ValueError("feedback recipient belongs to another user")
        if campaign.survey_type != FeedbackSurveyType.BUTTONS:
            raise ValueError("feedback campaign is not a button survey")

        option = get_button_option(campaign, button_value)
        if option is None:
            raise ValueError("unknown feedback button value")

        await repo.save_answer(
            session,
            recipient=recipient,
            answer_type=FeedbackAnswerType.BUTTON,
            button_value=button_value,
        )
        if button_requires_text(campaign, button_value):
            return FeedbackButtonAnswerResult(
                reward_id=None,
                reward_options=None,
                requires_text=True,
            )

        reward = await repo.issue_reward(
            session,
            campaign=campaign,
            recipient=recipient,
            expires_in=timedelta(days=texts.DEFAULT_REWARD_EXPIRES_DAYS),
        )
        return FeedbackButtonAnswerResult(
            reward_id=reward.id,
            reward_options=reward.reward_options,
            show_connection_support=button_shows_connection_support(
                campaign,
                button_value,
            ),
        )


async def save_button_text_answer_and_issue_reward(
    *,
    session_maker: async_sessionmaker,
    telegram_id: int,
    recipient_id: int,
    answer_text: str,
) -> tuple[int, list[dict]]:
    async with tx(session_maker) as session:
        context = await repo.get_recipient_with_campaign(session, recipient_id)
        if context is None:
            raise ValueError("feedback recipient not found")
        recipient, campaign = context
        if recipient.telegram_id_snapshot != telegram_id:
            raise ValueError("feedback recipient belongs to another user")
        if campaign.survey_type != FeedbackSurveyType.BUTTONS:
            raise ValueError("feedback campaign is not a button survey")

        cleaned_answer_text = answer_text.strip()
        if not cleaned_answer_text:
            raise ValueError("Напиши ответ текстом, пожалуйста.")

        answer_updated = await repo.update_answer_text(
            session,
            recipient_id=recipient.id,
            text_value=cleaned_answer_text,
        )
        if not answer_updated:
            raise ValueError("feedback answer not found")

        reward = await repo.issue_reward(
            session,
            campaign=campaign,
            recipient=recipient,
            expires_in=timedelta(days=texts.DEFAULT_REWARD_EXPIRES_DAYS),
        )
        return reward.id, reward.reward_options


async def save_pending_button_text_answer_and_issue_reward(
    *,
    session_maker: async_sessionmaker,
    telegram_id: int,
    answer_text: str,
) -> tuple[int, list[dict]] | tuple[None, None]:
    async with tx(session_maker) as session:
        context = await repo.find_pending_button_text_recipient(
            session,
            telegram_id,
            [texts.MISSING_LOCATION_BUTTON_VALUE, texts.OTHER_REASON_BUTTON_VALUE],
        )
        if context is None:
            return None, None
        recipient, campaign = context

        cleaned_answer_text = answer_text.strip()
        if not cleaned_answer_text:
            raise ValueError("Напиши ответ текстом, пожалуйста.")

        answer_updated = await repo.update_answer_text(
            session,
            recipient_id=recipient.id,
            text_value=cleaned_answer_text,
        )
        if not answer_updated:
            raise ValueError("feedback answer not found")

        reward = await repo.issue_reward(
            session,
            campaign=campaign,
            recipient=recipient,
            expires_in=timedelta(days=texts.DEFAULT_REWARD_EXPIRES_DAYS),
        )
        return reward.id, reward.reward_options


async def save_text_answer_and_issue_reward(
    *,
    session_maker: async_sessionmaker,
    telegram_id: int,
    answer_text: str,
) -> tuple[int, list[dict]] | tuple[None, None]:
    async with tx(session_maker) as session:
        context = await repo.find_pending_text_recipient(session, telegram_id)
        if context is None:
            return None, None
        recipient, campaign = context

        min_length = campaign.min_text_length or texts.DEFAULT_MIN_TEXT_LENGTH
        if len(answer_text.strip()) < min_length:
            raise ValueError(
                texts.TEXT_TOO_SHORT.format(
                    min_length=min_length,
                    actual_length=len(answer_text.strip()),
                )
            )

        await repo.save_answer(
            session,
            recipient=recipient,
            answer_type=FeedbackAnswerType.TEXT,
            text_value=answer_text.strip(),
        )
        reward = await repo.issue_reward(
            session,
            campaign=campaign,
            recipient=recipient,
            expires_in=timedelta(days=texts.DEFAULT_REWARD_EXPIRES_DAYS),
        )
        return reward.id, reward.reward_options
