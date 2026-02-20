import orjson
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from aiogram.types import Message
from common.rwms_client import RwmsClient

import proto.rwmanager_pb2 as proto
from utils.config import Config


async def create_user(
    rwms_client: RwmsClient,
    username: str,
    message: Message,
    config: Config,
    from_referrer: bool = False,
) -> Optional[proto.UserResponse]:
    description = {
        "is_bot": message.from_user.is_bot,
        "id": message.from_user.id,
        "username": message.from_user.username if message.from_user.username else None,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "language_code": message.from_user.language_code,
        "is_premium": message.from_user.is_premium,
        "added_to_attachment_menu": message.from_user.added_to_attachment_menu,
        "can_join_groups": message.from_user.can_join_groups,
        "can_read_all_group_messages": message.from_user.can_read_all_group_messages,
        "supports_inline_queries": message.from_user.supports_inline_queries,
        "can_connect_to_business": message.from_user.can_connect_to_business,
    }

    trial_period = (
        config.referral_bonus_days if from_referrer else config.trial_period_days
    )

    if from_referrer:
        logging.info(
            f"creating subscription {username} with referral bonus, trial period {trial_period} days"
        )

    response = await rwms_client.add_user(
        proto.AddUserRequest(
            username=username,
            telegram_id=message.from_user.id,
            expire_at=datetime.now(timezone.utc) + timedelta(days=trial_period),
            status=proto.UserStatus.ACTIVE,
            traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
            active_internal_squads=[*config.squads_uuids],
            created_at=datetime.now(),
            description=orjson.dumps(description),
        )
    )

    return response


async def update_user(
    rwms_client: RwmsClient,
    config: Config,
    user: proto.UserResponse,
    interval: timedelta,
) -> Tuple[Optional[proto.UserResponse], bool]:
    new_expire_at = None
    subscription_activated = False

    if user.HasField("expire_at"):
        new_expire_at = user.expire_at.ToDatetime(tzinfo=timezone.utc)

    if new_expire_at is None or new_expire_at < datetime.now(timezone.utc):
        new_expire_at = datetime.now(timezone.utc) + interval
        subscription_activated = True
    else:
        new_expire_at = new_expire_at + interval

    update_user_response = await rwms_client.update_user(
        proto.UpdateUserRequest(
            uuid=user.uuid,
            expire_at=new_expire_at,
            status=proto.UserStatus.ACTIVE,
            traffic_limit_strategy=proto.TrafficLimitStrategy.NO_RESET,
            active_internal_squads=[*config.squads_uuids],
        )
    )

    return update_user_response, subscription_activated
