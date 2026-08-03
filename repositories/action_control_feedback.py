from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def save_action_control_reply(
    session: AsyncSession,
    *,
    telegram_id: int,
    message_id: int,
    text_value: str,
    reply_to_message_id: int | None,
    bot_instance: str | None,
    max_age_days: int = 14,
) -> bool:
    cleaned_text = text_value.strip()
    if not cleaned_text:
        return False

    result = await session.execute(
        text(
            """
            with matched_delivery as (
                select
                    d.id as delivery_id,
                    d.user_id
                from action_control_deliveries d
                join users u on u.id = d.user_id
                where d.telegram_id_snapshot = :telegram_id
                  and d.status = 'sent'
                  and d.sent_at >= now() - (cast(:max_age_days as int) * interval '1 day')
                  and (:bot_instance is null or u.bot_instance = :bot_instance)
                  and (
                      (:reply_to_message_id is not null and d.message_id = :reply_to_message_id)
                      or (:reply_to_message_id is null and d.replied_at is null)
                  )
                order by
                    case
                        when :reply_to_message_id is not null and d.message_id = :reply_to_message_id then 0
                        else 1
                    end,
                    d.sent_at desc
                limit 1
            ),
            inserted_reply as (
                insert into action_control_replies (
                    delivery_id,
                    user_id,
                    telegram_id_snapshot,
                    message_id,
                    reply_to_message_id,
                    text_value,
                    text_length
                )
                select
                    delivery_id,
                    user_id,
                    :telegram_id,
                    :message_id,
                    :reply_to_message_id,
                    :text_value,
                    char_length(:text_value)
                from matched_delivery
                on conflict (telegram_id_snapshot, message_id) do nothing
                returning delivery_id
            )
            update action_control_deliveries d
            set replied_at = coalesce(d.replied_at, now()),
                updated_at = now()
            from inserted_reply r
            where d.id = r.delivery_id
            returning d.id
            """
        ),
        {
            "telegram_id": telegram_id,
            "message_id": message_id,
            "reply_to_message_id": reply_to_message_id,
            "text_value": cleaned_text,
            "bot_instance": bot_instance,
            "max_age_days": max_age_days,
        },
    )
    return result.first() is not None


async def get_action_control_stats(
    session: AsyncSession,
    *,
    days: int = 30,
    bot_instance: str | None = None,
) -> list[dict]:
    result = await session.execute(
        text(
            """
            select
                d.action_key,
                count(*) filter (where d.status = 'sent') as sent_count,
                count(distinct d.id) filter (where r.id is not null) as replied_count,
                round(
                    100.0 * count(distinct d.id) filter (where r.id is not null)
                    / nullif(count(*) filter (where d.status = 'sent'), 0),
                    1
                ) as reply_rate,
                count(distinct d.user_id) filter (
                    where yp.id is not null
                ) as paid_after_count
            from action_control_deliveries d
            join users u on u.id = d.user_id
            left join action_control_replies r on r.delivery_id = d.id
            left join yk_payments yp
              on yp.user_id = d.user_id
             and yp.status = 'succeeded'
             and d.sent_at is not null
             and yp.created_at > d.sent_at
            where d.created_at >= now() - (cast(:days as int) * interval '1 day')
              and (:bot_instance is null or u.bot_instance = :bot_instance)
            group by d.action_key
            order by d.action_key
            """
        ),
        {"days": days, "bot_instance": bot_instance},
    )
    return [dict(row._mapping) for row in result]


async def get_recent_action_control_replies(
    session: AsyncSession,
    *,
    limit: int = 10,
    bot_instance: str | None = None,
) -> list[dict]:
    result = await session.execute(
        text(
            """
            select
                r.created_at,
                d.action_key,
                r.telegram_id_snapshot,
                u.username,
                r.text_value
            from action_control_replies r
            join action_control_deliveries d on d.id = r.delivery_id
            join users u on u.id = r.user_id
            where (:bot_instance is null or u.bot_instance = :bot_instance)
            order by r.created_at desc
            limit :limit
            """
        ),
        {"limit": limit, "bot_instance": bot_instance},
    )
    return [dict(row._mapping) for row in result]
