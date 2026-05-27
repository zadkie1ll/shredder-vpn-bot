from datetime import datetime
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy import exists
from sqlalchemy import func
from sqlalchemy import not_
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.db import EventLog
from common.models.db import FeedbackAnswerType
from common.models.db import FeedbackCampaign
from common.models.db import FeedbackCampaignRecipient
from common.models.db import FeedbackCampaignRun
from common.models.db import FeedbackRecipientStatus
from common.models.db import FeedbackReferralFilter
from common.models.db import FeedbackReward
from common.models.db import FeedbackRewardStatus
from common.models.db import FeedbackRunMode
from common.models.db import FeedbackRunStatus
from common.models.db import FeedbackSurveyAnswer
from common.models.db import FeedbackSurveyType
from common.models.db import User
from common.models.db import UserTrafficProgress
from common.models.db import YkPayment

FEEDBACK_REWARD_SELECTED_VALUES = [
    FeedbackRewardStatus.SELECTED,
    FeedbackRewardStatus.SELECTED.value,
    FeedbackRewardStatus.SELECTED.name,
]
FEEDBACK_REWARD_USED_VALUES = [
    FeedbackRewardStatus.USED,
    FeedbackRewardStatus.USED.value,
    FeedbackRewardStatus.USED.name,
]


async def create_campaign_with_run(
    session: AsyncSession,
    *,
    title: str,
    survey_type: FeedbackSurveyType,
    min_text_length: int | None,
    message_text_key: str,
    button_options: list[dict],
    reward_options: list[dict],
    run_mode: FeedbackRunMode,
    created_by_telegram_id: int,
    test_telegram_id: int | None = None,
    user_limit: int | None = None,
) -> tuple[FeedbackCampaign, FeedbackCampaignRun]:
    campaign = FeedbackCampaign(
        slug=f"feedback-{uuid4().hex}",
        title=title,
        survey_type=survey_type,
        referral_filter=FeedbackReferralFilter.ALL,
        min_text_length=min_text_length,
        message_text_key=message_text_key,
        button_options=button_options,
        reward_options=reward_options,
        created_by_telegram_id=created_by_telegram_id,
    )
    session.add(campaign)
    await session.flush()

    run = FeedbackCampaignRun(
        campaign_id=campaign.id,
        run_mode=run_mode,
        test_telegram_id=test_telegram_id,
        user_limit=user_limit,
        status=FeedbackRunStatus.RUNNING,
        created_by_telegram_id=created_by_telegram_id,
        started_at=datetime.utcnow(),
    )
    session.add(run)
    await session.flush()

    return campaign, run


async def get_test_audience_user(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id).limit(1)
    )
    return result.scalar_one_or_none()


async def get_feedback_audience(
    session: AsyncSession,
    limit: int,
) -> list[User]:
    paid_payment_exists = exists().where(
        and_(
            YkPayment.user_id == User.id,
            YkPayment.status == "succeeded",
        )
    )
    subscription_created_exists = exists().where(
        and_(
            EventLog.user_id == User.id,
            EventLog.event_type == "subscription_created",
        )
    )
    connected_exists = exists().where(
        and_(
            UserTrafficProgress.user_id == User.id,
            UserTrafficProgress.passed_0.is_(True),
        )
    )
    already_sent_feedback_exists = exists().where(
        and_(
            FeedbackCampaignRecipient.user_id == User.id,
            FeedbackCampaignRecipient.status.in_(
                [
                    FeedbackRecipientStatus.SENT,
                    FeedbackRecipientStatus.ANSWERED,
                    FeedbackRecipientStatus.REWARDED,
                ]
            ),
            FeedbackCampaignRecipient.run_id == FeedbackCampaignRun.id,
            FeedbackCampaignRun.run_mode == FeedbackRunMode.NEAREST_EXPIRING,
        )
    )

    result = await session.execute(
        select(User)
        .where(
            and_(
                User.telegram_id > 0,
                User.expire_at.isnot(None),
                User.expire_at <= func.now(),
                subscription_created_exists,
                connected_exists,
                not_(paid_payment_exists),
                not_(already_sent_feedback_exists),
            )
        )
        .order_by(User.expire_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_users_by_telegram_ids(
    session: AsyncSession,
    telegram_ids: list[int],
) -> list[User]:
    if not telegram_ids:
        return []

    result = await session.execute(
        select(User).where(User.telegram_id.in_(telegram_ids))
    )
    users_by_telegram_id = {user.telegram_id: user for user in result.scalars().all()}
    return [
        users_by_telegram_id[telegram_id]
        for telegram_id in telegram_ids
        if telegram_id in users_by_telegram_id
    ]


async def create_recipient(
    session: AsyncSession,
    *,
    campaign_id: int,
    run_id: int,
    user: User,
) -> FeedbackCampaignRecipient:
    recipient = FeedbackCampaignRecipient(
        campaign_id=campaign_id,
        run_id=run_id,
        user_id=user.id,
        telegram_id_snapshot=user.telegram_id,
        expire_at_snapshot=user.expire_at,
        status=FeedbackRecipientStatus.QUEUED,
    )
    session.add(recipient)
    await session.flush()
    return recipient


async def mark_recipient_sent(
    session: AsyncSession,
    recipient_id: int,
    message_id: int,
) -> None:
    await session.execute(
        update(FeedbackCampaignRecipient)
        .where(FeedbackCampaignRecipient.id == recipient_id)
        .values(
            status=FeedbackRecipientStatus.SENT,
            sent_message_id=message_id,
            sent_at=datetime.utcnow(),
        )
    )


async def mark_recipient_failed(
    session: AsyncSession,
    recipient_id: int,
    error: str,
) -> None:
    await session.execute(
        update(FeedbackCampaignRecipient)
        .where(FeedbackCampaignRecipient.id == recipient_id)
        .values(
            status=FeedbackRecipientStatus.FAILED,
            failed_at=datetime.utcnow(),
            error=error[:2000],
        )
    )


async def finish_run(session: AsyncSession, run_id: int) -> None:
    counts = await get_run_counts(session, run_id)
    await session.execute(
        update(FeedbackCampaignRun)
        .where(FeedbackCampaignRun.id == run_id)
        .values(
            status=FeedbackRunStatus.FINISHED,
            finished_at=datetime.utcnow(),
            sent_count=counts["sent"],
            answered_count=counts["answered"],
            rewarded_count=counts["rewarded"],
            failed_count=counts["failed"],
        )
    )


async def cancel_run(session: AsyncSession, run_id: int) -> bool:
    result = await session.execute(
        update(FeedbackCampaignRun)
        .where(FeedbackCampaignRun.id == run_id)
        .values(
            status=FeedbackRunStatus.CANCELLED,
            finished_at=datetime.utcnow(),
        )
    )
    return result.rowcount > 0


async def cleanup_old_production_recipients(
    session: AsyncSession,
    older_than_days: int,
) -> int:
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    production_run_ids = (
        select(FeedbackCampaignRun.id)
        .where(FeedbackCampaignRun.run_mode == FeedbackRunMode.NEAREST_EXPIRING)
        .scalar_subquery()
    )

    result = await session.execute(
        update(FeedbackCampaignRecipient)
        .where(
            and_(
                FeedbackCampaignRecipient.run_id.in_(production_run_ids),
                FeedbackCampaignRecipient.created_at <= cutoff,
                FeedbackCampaignRecipient.status.in_(
                    [
                        FeedbackRecipientStatus.SENT,
                        FeedbackRecipientStatus.ANSWERED,
                        FeedbackRecipientStatus.REWARDED,
                    ]
                ),
            )
        )
        .values(
            status=FeedbackRecipientStatus.SKIPPED,
            error=f"cleaned up after {older_than_days} days",
        )
    )
    return result.rowcount or 0


async def get_run_counts(session: AsyncSession, run_id: int) -> dict[str, int]:
    result = await session.execute(
        select(
            FeedbackCampaignRecipient.status, func.count(FeedbackCampaignRecipient.id)
        )
        .where(FeedbackCampaignRecipient.run_id == run_id)
        .group_by(FeedbackCampaignRecipient.status)
    )
    raw_counts = {status: count for status, count in result.all()}
    return {
        "sent": raw_counts.get(FeedbackRecipientStatus.SENT, 0)
        + raw_counts.get(FeedbackRecipientStatus.ANSWERED, 0)
        + raw_counts.get(FeedbackRecipientStatus.REWARDED, 0),
        "answered": raw_counts.get(FeedbackRecipientStatus.ANSWERED, 0)
        + raw_counts.get(FeedbackRecipientStatus.REWARDED, 0),
        "rewarded": raw_counts.get(FeedbackRecipientStatus.REWARDED, 0),
        "failed": raw_counts.get(FeedbackRecipientStatus.FAILED, 0),
    }


async def get_run_with_campaign(
    session: AsyncSession,
    run_id: int,
) -> tuple[FeedbackCampaignRun, FeedbackCampaign] | None:
    result = await session.execute(
        select(FeedbackCampaignRun, FeedbackCampaign)
        .join(
            FeedbackCampaign,
            FeedbackCampaign.id == FeedbackCampaignRun.campaign_id,
        )
        .where(FeedbackCampaignRun.id == run_id)
        .limit(1)
    )
    return result.one_or_none()


async def get_production_run_summaries(
    session: AsyncSession,
    limit: int,
) -> list[dict]:
    audience_count = (
        select(func.count(FeedbackCampaignRecipient.id))
        .where(FeedbackCampaignRecipient.run_id == FeedbackCampaignRun.id)
        .scalar_subquery()
    )
    sent_count = (
        select(func.count(FeedbackCampaignRecipient.id))
        .where(
            and_(
                FeedbackCampaignRecipient.run_id == FeedbackCampaignRun.id,
                FeedbackCampaignRecipient.status.in_(
                    [
                        FeedbackRecipientStatus.SENT,
                        FeedbackRecipientStatus.ANSWERED,
                        FeedbackRecipientStatus.REWARDED,
                    ]
                ),
            )
        )
        .scalar_subquery()
    )
    answered_count = (
        select(func.count(FeedbackCampaignRecipient.id))
        .where(
            and_(
                FeedbackCampaignRecipient.run_id == FeedbackCampaignRun.id,
                FeedbackCampaignRecipient.status.in_(
                    [
                        FeedbackRecipientStatus.ANSWERED,
                        FeedbackRecipientStatus.REWARDED,
                    ]
                ),
            )
        )
        .scalar_subquery()
    )
    paid_count = (
        select(func.count(FeedbackReward.id))
        .join(
            FeedbackCampaignRecipient,
            FeedbackCampaignRecipient.id == FeedbackReward.recipient_id,
        )
        .where(
            and_(
                FeedbackCampaignRecipient.run_id == FeedbackCampaignRun.id,
                FeedbackReward.status.in_(FEEDBACK_REWARD_USED_VALUES),
            )
        )
        .scalar_subquery()
    )

    result = await session.execute(
        select(
            FeedbackCampaignRun.id.label("run_id"),
            FeedbackCampaignRun.status.label("status"),
            FeedbackCampaignRun.created_at.label("created_at"),
            FeedbackCampaignRun.started_at.label("started_at"),
            FeedbackCampaignRun.finished_at.label("finished_at"),
            FeedbackCampaignRun.user_limit.label("user_limit"),
            FeedbackCampaign.survey_type.label("survey_type"),
            FeedbackCampaign.reward_options.label("reward_options"),
            audience_count.label("audience_count"),
            sent_count.label("sent_count"),
            answered_count.label("answered_count"),
            paid_count.label("paid_count"),
        )
        .join(
            FeedbackCampaign,
            FeedbackCampaign.id == FeedbackCampaignRun.campaign_id,
        )
        .where(FeedbackCampaignRun.run_mode == FeedbackRunMode.NEAREST_EXPIRING)
        .order_by(FeedbackCampaignRun.created_at.desc())
        .limit(limit)
    )
    return [dict(row) for row in result.mappings().all()]


async def get_button_answer_stats(
    session: AsyncSession,
    run_id: int,
) -> dict[int, int]:
    result = await session.execute(
        select(
            FeedbackSurveyAnswer.button_value,
            func.count(FeedbackSurveyAnswer.id),
        )
        .where(
            and_(
                FeedbackSurveyAnswer.run_id == run_id,
                FeedbackSurveyAnswer.answer_type == FeedbackAnswerType.BUTTON,
                FeedbackSurveyAnswer.is_valid.is_(True),
            )
        )
        .group_by(FeedbackSurveyAnswer.button_value)
        .order_by(FeedbackSurveyAnswer.button_value)
    )
    return {button_value: count for button_value, count in result.all()}


async def get_reward_period_stats(
    session: AsyncSession,
    run_id: int,
) -> dict[str, dict[str, int]]:
    result = await session.execute(
        select(
            FeedbackReward.selected_subscription_period,
            FeedbackReward.status,
            func.count(FeedbackReward.id),
        )
        .join(
            FeedbackCampaignRecipient,
            FeedbackCampaignRecipient.id == FeedbackReward.recipient_id,
        )
        .where(
            and_(
                FeedbackCampaignRecipient.run_id == run_id,
                FeedbackReward.selected_subscription_period.isnot(None),
            )
        )
        .group_by(
            FeedbackReward.selected_subscription_period,
            FeedbackReward.status,
        )
    )

    stats: dict[str, dict[str, int]] = {}
    for period, status, count in result.all():
        period_stats = stats.setdefault(period, {"selected": 0, "used": 0})
        status_value = getattr(status, "value", status)
        if status_value in {
            FeedbackRewardStatus.USED.value,
            FeedbackRewardStatus.USED.name,
        }:
            period_stats["used"] += count
            period_stats["selected"] += count
        elif status_value in {
            FeedbackRewardStatus.SELECTED.value,
            FeedbackRewardStatus.SELECTED.name,
        }:
            period_stats["selected"] += count
    return stats


async def get_text_answer_count(
    session: AsyncSession,
    run_id: int,
) -> int:
    result = await session.execute(
        select(func.count(FeedbackSurveyAnswer.id)).where(
            and_(
                FeedbackSurveyAnswer.run_id == run_id,
                FeedbackSurveyAnswer.answer_type == FeedbackAnswerType.TEXT,
                FeedbackSurveyAnswer.is_valid.is_(True),
            )
        )
    )
    return result.scalar_one()


async def get_text_answer_page(
    session: AsyncSession,
    run_id: int,
    page: int,
) -> dict | None:
    result = await session.execute(
        select(
            FeedbackSurveyAnswer.id.label("answer_id"),
            FeedbackSurveyAnswer.user_id.label("user_id"),
            FeedbackSurveyAnswer.telegram_id_snapshot.label("telegram_id"),
            FeedbackSurveyAnswer.text_value.label("text_value"),
            FeedbackSurveyAnswer.text_length.label("text_length"),
            FeedbackSurveyAnswer.created_at.label("created_at"),
            User.telegram_username.label("telegram_username"),
        )
        .outerjoin(User, User.id == FeedbackSurveyAnswer.user_id)
        .where(
            and_(
                FeedbackSurveyAnswer.run_id == run_id,
                FeedbackSurveyAnswer.answer_type == FeedbackAnswerType.TEXT,
                FeedbackSurveyAnswer.is_valid.is_(True),
            )
        )
        .order_by(FeedbackSurveyAnswer.created_at.asc())
        .offset(page)
        .limit(1)
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_recipient_with_campaign(
    session: AsyncSession,
    recipient_id: int,
) -> tuple[FeedbackCampaignRecipient, FeedbackCampaign] | None:
    result = await session.execute(
        select(FeedbackCampaignRecipient, FeedbackCampaign)
        .join(
            FeedbackCampaign,
            FeedbackCampaign.id == FeedbackCampaignRecipient.campaign_id,
        )
        .where(FeedbackCampaignRecipient.id == recipient_id)
        .limit(1)
    )
    return result.one_or_none()


async def find_pending_text_recipient(
    session: AsyncSession,
    telegram_id: int,
) -> tuple[FeedbackCampaignRecipient, FeedbackCampaign] | None:
    result = await session.execute(
        select(FeedbackCampaignRecipient, FeedbackCampaign)
        .join(
            FeedbackCampaign,
            FeedbackCampaign.id == FeedbackCampaignRecipient.campaign_id,
        )
        .where(
            and_(
                FeedbackCampaignRecipient.telegram_id_snapshot == telegram_id,
                FeedbackCampaignRecipient.status == FeedbackRecipientStatus.SENT,
                FeedbackCampaign.survey_type == FeedbackSurveyType.TEXT,
            )
        )
        .order_by(FeedbackCampaignRecipient.sent_at.desc())
        .limit(1)
    )
    return result.one_or_none()


async def save_answer(
    session: AsyncSession,
    *,
    recipient: FeedbackCampaignRecipient,
    answer_type: FeedbackAnswerType,
    button_value: int | None = None,
    text_value: str | None = None,
    is_valid: bool = True,
) -> FeedbackSurveyAnswer:
    answer = FeedbackSurveyAnswer(
        campaign_id=recipient.campaign_id,
        run_id=recipient.run_id,
        recipient_id=recipient.id,
        user_id=recipient.user_id,
        telegram_id_snapshot=recipient.telegram_id_snapshot,
        answer_type=answer_type,
        button_value=button_value,
        text_value=text_value,
        text_length=len(text_value) if text_value is not None else None,
        is_valid=is_valid,
    )
    session.add(answer)
    await session.execute(
        update(FeedbackCampaignRecipient)
        .where(FeedbackCampaignRecipient.id == recipient.id)
        .values(
            status=FeedbackRecipientStatus.ANSWERED,
            answered_at=datetime.utcnow(),
        )
    )
    await session.flush()
    return answer


async def issue_reward(
    session: AsyncSession,
    *,
    campaign: FeedbackCampaign,
    recipient: FeedbackCampaignRecipient,
    expires_in: timedelta,
) -> FeedbackReward:
    existing = await session.execute(
        select(FeedbackReward)
        .where(FeedbackReward.recipient_id == recipient.id)
        .limit(1)
    )
    reward = existing.scalar_one_or_none()
    if reward is not None:
        return reward

    reward = FeedbackReward(
        campaign_id=campaign.id,
        recipient_id=recipient.id,
        user_id=recipient.user_id,
        reward_options=campaign.reward_options,
        code=f"fb_{uuid4().hex}",
        status=FeedbackRewardStatus.ISSUED,
        expires_at=datetime.utcnow() + expires_in,
    )
    session.add(reward)
    await session.execute(
        update(FeedbackCampaignRecipient)
        .where(FeedbackCampaignRecipient.id == recipient.id)
        .values(
            status=FeedbackRecipientStatus.REWARDED,
            rewarded_at=datetime.utcnow(),
        )
    )
    await session.flush()
    return reward


async def get_reward_for_user(
    session: AsyncSession,
    *,
    reward_id: int,
    telegram_id: int,
) -> FeedbackReward | None:
    result = await session.execute(
        select(FeedbackReward)
        .join(User, User.id == FeedbackReward.user_id)
        .where(
            and_(
                FeedbackReward.id == reward_id,
                User.telegram_id == telegram_id,
                FeedbackReward.status.in_(
                    [FeedbackRewardStatus.ISSUED, FeedbackRewardStatus.SELECTED]
                ),
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def mark_reward_selected(
    session: AsyncSession,
    *,
    reward_id: int,
    subscription_period: str,
    discount_percent: int | None,
    discount_amount: int | None,
) -> None:
    await session.execute(
        update(FeedbackReward)
        .where(FeedbackReward.id == reward_id)
        .values(
            status=FeedbackRewardStatus.SELECTED,
            selected_subscription_period=subscription_period,
            selected_discount_percent=discount_percent,
            selected_discount_amount=discount_amount,
        )
    )
