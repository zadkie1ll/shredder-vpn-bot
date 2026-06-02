from datetime import timedelta

from sqlalchemy import and_
from sqlalchemy import select
from sqlalchemy.orm import aliased

from common.models.db import ReferralBonus
from common.models.db import ReferralBonusType
from common.models.db import ReferralType
from common.models.db import User
from common.models.db import YkPayment
from common.models.tariff import str_to_tariff
from common.rwms_client import RwmsClient
from utils.config import Config
from utils.rwms_helpers import update_user
from utils.sql_helpers import extend_user_subscription_by_tg_id


async def award_referral_purchase_bonus(
    session,
    rwms_client: RwmsClient,
    config: Config,
    referral_tg_id: int,
    interval: timedelta,
    referral_type: ReferralType | None = None,
) -> dict:
    referral_user = aliased(User)
    referrer_user = aliased(User)

    result = await session.execute(
        select(referral_user, referrer_user)
        .outerjoin(referrer_user, referral_user.referred_by_id == referrer_user.id)
        .where(referral_user.telegram_id == referral_tg_id)
        .limit(1)
    )
    row = result.first()

    if row is None:
        return {"status": "referral_not_found"}

    referral, referrer = row
    if referrer is None:
        return {"status": "no_referrer", "referral": referral}

    if referral_type is not None and referral.referral_type != referral_type:
        return {
            "status": "wrong_referral_type",
            "referral": referral,
            "referrer": referrer,
        }

    has_success_payment = await session.scalar(
        select(YkPayment.id)
        .where(
            and_(
                YkPayment.user_id == referral.id,
                YkPayment.status == "succeeded",
            )
        )
        .limit(1)
    )
    if has_success_payment is None:
        return {"status": "no_payment", "referral": referral, "referrer": referrer}

    already_awarded = await session.scalar(
        select(ReferralBonus.id)
        .where(
            and_(
                ReferralBonus.referral_id == referral.id,
                ReferralBonus.bonus_type == ReferralBonusType.PURCHASE,
            )
        )
        .limit(1)
    )
    if already_awarded is not None:
        return {
            "status": "already_awarded",
            "referral": referral,
            "referrer": referrer,
        }

    referrer_username = referrer.username or str(referrer.telegram_id)
    rwms_user = await rwms_client.get_user_by_username(referrer_username)
    if rwms_user is None:
        return {
            "status": "rwms_referrer_not_found",
            "referral": referral,
            "referrer": referrer,
        }

    user_response, _ = await update_user(
        rwms_client=rwms_client,
        config=config,
        user=rwms_user,
        interval=interval,
    )
    if user_response is None:
        return {
            "status": "rwms_update_failed",
            "referral": referral,
            "referrer": referrer,
        }

    await extend_user_subscription_by_tg_id(
        session=session,
        telegram_id=referrer.telegram_id,
        interval=interval,
    )
    session.add(
        ReferralBonus(
            referral_id=referral.id,
            referrer_id=referrer.id,
            bonus_type=ReferralBonusType.PURCHASE,
            days_added=interval.days,
        )
    )

    return {
        "status": "ok",
        "referral": referral,
        "referrer": referrer,
        "days": interval.days,
    }


async def award_sales_referral_purchase_bonus(
    session,
    rwms_client: RwmsClient,
    config: Config,
    referral_tg_id: int,
) -> dict:
    referral = await session.scalar(
        select(User).where(User.telegram_id == referral_tg_id).limit(1)
    )

    if referral is None:
        return {"status": "referral_not_found"}

    payment = await session.scalar(
        select(YkPayment)
        .where(
            and_(
                YkPayment.user_id == referral.id,
                YkPayment.status == "succeeded",
            )
        )
        .order_by(YkPayment.created_at.asc())
        .limit(1)
    )
    if payment is None:
        return {"status": "no_payment", "referral": referral}

    tariff = str_to_tariff(payment.subscription_period)
    if tariff is None:
        return {
            "status": "unknown_tariff",
            "referral": referral,
            "subscription_period": payment.subscription_period,
        }

    result = await award_referral_purchase_bonus(
        session=session,
        rwms_client=rwms_client,
        config=config,
        referral_tg_id=referral_tg_id,
        interval=tariff.subscription_period,
        referral_type=ReferralType.SALES_PURCHASE,
    )
    result["referral_tariff"] = payment.subscription_period
    return result
