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


@dataclass
class FeedbackSendResult:
    run_id: int
    selected_count: int
    sent_count: int
    failed_count: int


def parse_reward_options(value: str) -> list[dict]:
    periods = []
    for raw_period in value.split(","):
        period = raw_period.strip().lower()
        if not period:
            continue
        if period not in ALLOWED_REWARD_PERIODS:
            raise ValueError("allowed reward periods: month,sixmonths,year")
        if period not in periods:
            periods.append(period)

    if not periods:
        raise ValueError("at least one reward period is required")

    return [
        {
            "subscription_period": period,
            "discount_percent": texts.DEFAULT_DISCOUNT_PERCENT,
        }
        for period in periods
    ]


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
        if option["subscription_period"] == subscription_period:
            return option
    return None


def discounted_price(option: dict) -> int:
    tariff = str_to_tariff(option["subscription_period"])
    discount_percent = option.get("discount_percent") or 0
    discount_amount = option.get("discount_amount") or 0
    if discount_percent:
        return max(1, round(tariff.price * (100 - discount_percent) / 100))
    return max(1, tariff.price - discount_amount)


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
            button_options=texts.SURVEY_BUTTON_OPTIONS,
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
) -> FeedbackSendResult:
    async with tx(session_maker) as session:
        users = await repo.get_feedback_audience(session, limit)
        campaign, run = await repo.create_campaign_with_run(
            session,
            title="Trial feedback",
            survey_type=survey_type,
            min_text_length=min_text_length,
            message_text_key=survey_type.value,
            button_options=texts.SURVEY_BUTTON_OPTIONS,
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
) -> FeedbackSendResult:
    async with tx(session_maker) as session:
        users = await repo.get_users_by_telegram_ids(session, telegram_ids)
        campaign, run = await repo.create_campaign_with_run(
            session,
            title="Trial feedback",
            survey_type=survey_type,
            min_text_length=min_text_length,
            message_text_key=survey_type.value,
            button_options=texts.SURVEY_BUTTON_OPTIONS,
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

        allowed_values = {option["value"] for option in campaign.button_options}
        if button_value not in allowed_values:
            raise ValueError("unknown feedback button value")

        await repo.save_answer(
            session,
            recipient=recipient,
            answer_type=FeedbackAnswerType.BUTTON,
            button_value=button_value,
        )
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
