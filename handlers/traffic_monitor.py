import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import proto.rwmanager_pb2 as proto
from common.models.db import User, UserTrafficAnomaly
from common.rwms_client import RwmsClient
from filters.is_admin import IsAdmin

BLOCK_CALLBACK_PREFIX = "block_user:"

traffic_monitor_router = Router()


@traffic_monitor_router.callback_query(
    F.data.startswith(BLOCK_CALLBACK_PREFIX),
    IsAdmin(),
)
async def block_traffic_anomaly_user(
    query: CallbackQuery,
    session_maker: async_sessionmaker,
    rwms_client: RwmsClient,
) -> None:
    try:
        user_id = int(query.data.removeprefix(BLOCK_CALLBACK_PREFIX))
    except (AttributeError, ValueError):
        await query.answer("Некорректный пользователь", show_alert=True)
        return

    async with session_maker() as session:
        user = await session.get(User, user_id)
        if user is None or not user.username:
            await query.answer("Пользователь не найден", show_alert=True)
            return

        rwms_user = await rwms_client.get_user_by_username(user.username)
        if rwms_user is None:
            await query.answer("Пользователь не найден в RWMS", show_alert=True)
            return

        response = await rwms_client.update_user(
            proto.UpdateUserRequest(
                uuid=rwms_user.uuid,
                status=proto.UserStatus.DISABLED,
                active_internal_squads=[
                    squad.uuid for squad in rwms_user.active_internal_squads
                ],
            )
        )
        if response is None:
            await query.answer(
                "Не удалось заблокировать пользователя",
                show_alert=True,
            )
            return

        snapshot = await session.scalar(
            select(UserTrafficAnomaly).where(UserTrafficAnomaly.user_id == user_id)
        )
        if snapshot is not None:
            snapshot.is_blocked = True
            await session.commit()

    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(
            text="Пользователь заблокирован",
            callback_data="traffic_monitor:done",
        )
    )
    if query.message is not None:
        await query.message.edit_reply_markup(reply_markup=keyboard.as_markup())
    await query.answer("Пользователь заблокирован")
    logging.info(
        "traffic monitor user blocked by admin=%s user_id=%s username=%s",
        query.from_user.id,
        user_id,
        user.username,
    )
