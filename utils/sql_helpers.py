import logging
from typing import Optional
from datetime import datetime
from datetime import timedelta
from contextlib import asynccontextmanager

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy import text
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.db import User
from common.models.db import EventLog
from common.models.db import YkPayment
from common.models.db import YkRecurrentPayment
from common.models.db import ReferralBonus
from common.models.db import ReferralType
from common.models.db import ReferralBonusType

from common.models.analytics_event import AnalyticsEvent


@asynccontextmanager
async def tx(session_maker):
    async with session_maker() as session:
        async with session.begin():
            yield session


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> Optional[User]:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id).limit(1)
    )
    return result.scalar_one_or_none()


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    result = await session.execute(
        select(User).where(User.username == username).limit(1)
    )
    return result.scalar_one_or_none()


async def save_user_in_db(
    session: AsyncSession,
    username: str,  # this parameter is required (must not be empty)
    referrer_id: Optional[int],
    telegram_id: Optional[int],
    expire_at: Optional[datetime],
) -> User:
    query = text("""
        INSERT INTO users (
            telegram_id,
            username,
            referred_by_id,
            referral_type,
            expire_at
        ) VALUES (
            :telegram_id,
            :username,
            :referred_by_id,
            :referral_type,
            (:expire_at)::timestamp
        )
        ON CONFLICT (telegram_id) DO UPDATE SET username = :username, expire_at = (:expire_at)::timestamp
        RETURNING *
    """)

    logging.info(
        f"Saving user in DB with telegram_id={telegram_id}, "
        f"username={username}, referrer_id={referrer_id}, expire_at={expire_at}"
    )

    result = await session.execute(
        query,
        {
            "telegram_id": telegram_id,
            "expire_at": expire_at,
            "username": username,
            "referred_by_id": referrer_id,
            "referral_type": ReferralType.STANDARD if referrer_id is not None else None,
        },
    )

    row = result.mappings().first()

    # Создаем объект User из полученных данных
    if row:
        user = User(**row)
        logging.info(f"user saved in DB: {user}")
        return user

    # Эта ситуация маловероятна из-за INSERT/UPDATE, но на всякий случай
    raise ValueError("User was not created or updated")


async def add_user_to_traffic_progress(session: AsyncSession, telegram_id: int) -> None:
    query = text("""
            INSERT INTO user_traffic_progress (user_id)
            SELECT id FROM users WHERE telegram_id = :telegram_id
            ON CONFLICT (user_id) DO NOTHING
        """)

    await session.execute(query, {"telegram_id": telegram_id})


async def has_payment_for_user_by_tg_id(
    session: AsyncSession, telegram_id: int
) -> bool:
    result = await session.execute(
        select(YkPayment)
        .join(User, User.id == YkPayment.user_id)
        .where(User.telegram_id == telegram_id, YkPayment.status == "succeeded")
    )

    return result.scalars().first() is not None


async def has_saved_notification(
    session: AsyncSession, telegram_id: int, notification_type: str
) -> bool:
    if notification_type == "subscription-expired":
        query = text("""
            SELECT 1
            FROM expired_users_notifications eun
            JOIN users u ON u.id = eun.user_id
            WHERE u.telegram_id = :telegram_id
            LIMIT 1
        """)
    elif notification_type == "nc-yesterday-created":
        query = text("""
            SELECT 1
            FROM nc_users_notifications nun
            JOIN users u ON u.id = nun.user_id
            WHERE u.telegram_id = :telegram_id
            LIMIT 1
        """)
    elif notification_type == "1-day-left":
        query = text("""
            SELECT 1
            FROM extend_subscription_notifications esn
            JOIN users u ON u.id = esn.user_id
            WHERE u.telegram_id = :telegram_id AND esn.one_day_before = TRUE
            LIMIT 1
        """)
    elif notification_type == "3-days-left":
        query = text("""
            SELECT 1
            FROM extend_subscription_notifications esn
            JOIN users u ON u.id = esn.user_id
            WHERE u.telegram_id = :telegram_id AND esn.three_days_before = TRUE
            LIMIT 1
        """)
    else:
        return False

    result = await session.execute(query, {"telegram_id": telegram_id})
    return result.first() is not None


async def has_autopay(session: AsyncSession, telegram_id: int) -> bool:
    stmt = (
        select(YkRecurrentPayment)
        .join(User, User.id == YkRecurrentPayment.user_id)
        .where(User.telegram_id == telegram_id)
    )

    result = await session.execute(stmt)
    return result.scalar() is not None


async def remove_autopay(session: AsyncSession, telegram_id: int) -> None:
    subquery = select(User.id).where(User.telegram_id == telegram_id).scalar_subquery()

    await session.execute(
        delete(YkRecurrentPayment).where(YkRecurrentPayment.user_id == subquery)
    )


async def turn_on_autopay_allow(session: AsyncSession, telegram_id: int) -> None:
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(autopay_allow=True)
    )


async def turn_off_autopay_allow(session: AsyncSession, telegram_id: int) -> None:
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(autopay_allow=False)
    )


async def save_notified_expired_user(session: AsyncSession, telegram_id: int) -> None:
    query = text("""
        INSERT INTO expired_users_notifications (user_id)
        SELECT id FROM users WHERE telegram_id = :telegram_id ON CONFLICT (user_id) DO NOTHING
    """)

    await session.execute(query, {"telegram_id": telegram_id})


async def save_notified_one_day_left_user(
    session: AsyncSession, telegram_id: int
) -> None:
    query = text("""
        INSERT INTO extend_subscription_notifications (user_id, one_day_before)
        SELECT id, :flag FROM users WHERE telegram_id = :telegram_id ON CONFLICT (user_id) DO
        UPDATE SET one_day_before = :flag
    """)

    await session.execute(query, {"telegram_id": telegram_id, "flag": True})


async def save_notified_three_days_left_user(
    session: AsyncSession, telegram_id: int
) -> None:
    query = text("""
        INSERT INTO extend_subscription_notifications (user_id, three_days_before)
        SELECT id, :flag FROM users WHERE telegram_id = :telegram_id ON CONFLICT (user_id) DO
        UPDATE SET three_days_before = :flag
    """)

    await session.execute(query, {"telegram_id": telegram_id, "flag": True})


async def save_notified_nc_user(session: AsyncSession, telegram_id: int) -> None:
    query = text("""
        INSERT INTO nc_users_notifications (user_id)
        SELECT id FROM users WHERE telegram_id = :telegram_id ON CONFLICT (user_id) DO NOTHING
    """)

    await session.execute(query, {"telegram_id": telegram_id})


async def update_user_ymid(session: AsyncSession, telegram_id: int, ymid: int) -> None:
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(ymid=ymid)
    )


async def add_event_log(
    session: AsyncSession, event: AnalyticsEvent, username: str
) -> None:
    user_id = await session.scalar(
        select(User.id).where(User.username == username).limit(1)
    )

    if user_id is None:
        logging.error(f"not found user id for username {username}")
        return

    session.add(
        EventLog(
            user_id=user_id,
            event_type=event.event_type,
            event_payload=event.model_dump(),
        )
    )


async def get_all_users(session: AsyncSession):
    result = await session.execute(select(User.telegram_id))
    return result.scalars().all()


async def get_event_logs(session: AsyncSession):
    result = await session.execute(select(EventLog).order_by(EventLog.timestamp.asc()))
    return result.scalars().all()


async def extend_user_subscription_by_tg_id(
    session: AsyncSession,
    telegram_id: int,
    interval: timedelta,
) -> None:
    extend_expire_at_query = text("""
        UPDATE users 
            SET expire_at = 
            CASE 
                WHEN expire_at > (NOW() AT TIME ZONE 'UTC') THEN expire_at + (:interval)::interval
                ELSE (NOW() AT TIME ZONE 'UTC') + (:interval)::interval
            END
            WHERE telegram_id = :telegram_id
        """)

    await session.execute(
        extend_expire_at_query,
        {
            "telegram_id": telegram_id,
            "interval": interval,
        },
    )


async def extend_user_subscription_by_username(
    session: AsyncSession,
    username: str,
    interval: timedelta,
) -> None:
    extend_expire_at_query = text("""
        UPDATE users 
            SET expire_at = 
            CASE 
                WHEN expire_at > (NOW() AT TIME ZONE 'UTC') THEN expire_at + (:interval)::interval
                ELSE (NOW() AT TIME ZONE 'UTC') + (:interval)::interval
            END
            WHERE username = :username
        """)

    await session.execute(
        extend_expire_at_query,
        {
            "username": username,
            "interval": interval,
        },
    )


async def get_all_recurrents(session: AsyncSession):
    result = await session.execute(select(YkRecurrentPayment))

    return result.scalars().all()


async def get_last_traffic_source_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> tuple[bool, Optional[int]]:
    # Возвращает (has_event, traffic_source)
    # has_event: True если найдено событие, False если событий нет
    # traffic_source: значение traffic_source (может быть None)

    # 1. Получаем user_id из таблицы users
    user_result = await session.execute(
        select(User.id).where(User.telegram_id == telegram_id)
    )
    user_id = user_result.scalar_one_or_none()

    if not user_id:
        return False, None

    # 2. Получаем последний traffic_source
    event_result = await session.execute(
        select(EventLog)
        .where(
            (EventLog.user_id == user_id)
            & (
                EventLog.event_type.in_(
                    ["subscription_created", "traffic_source_changed"]
                )
            )
        )
        .order_by(EventLog.timestamp.desc())
        .limit(1)
    )

    event = event_result.scalar_one_or_none()

    if event:
        return True, event.event_payload.get("traffic_source")

    return False, None


async def get_number_of_invited_referrals(session: AsyncSession, username: str) -> int:
    subquery = (
        select(User.id).where(User.username == username).limit(1).scalar_subquery()
    )

    result = await session.execute(
        select(func.count(User.id)).where(User.referred_by_id == subquery)
    )

    return result.scalar()


async def get_referral_bonuses_for_user(session: AsyncSession, username: str) -> int:
    subquery = (
        select(User.id).where(User.username == username).limit(1).scalar_subquery()
    )

    result = await session.execute(
        select(func.coalesce(func.sum(ReferralBonus.days_added), 0)).where(
            ReferralBonus.referrer_id == subquery
        )
    )

    return result.scalar()
