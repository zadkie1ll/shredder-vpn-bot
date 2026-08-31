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
                  and (cast(:bot_instance as text) is null or u.bot_instance = cast(:bot_instance as text))
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
            with base as (
                select
                    d.*,
                    nullif(d.trigger_payload->>'feedback_reward_id', '')::bigint as reward_id
                from action_control_deliveries d
                join users u on u.id = d.user_id
                where d.created_at >= now() - (cast(:days as int) * interval '1 day')
                  and (cast(:bot_instance as text) is null or u.bot_instance = cast(:bot_instance as text))
            ),
            replies as (
                select
                    delivery_id,
                    count(*) as reply_count
                from action_control_replies
                group by delivery_id
            ),
            paid_after as (
                select
                    b.id as delivery_id,
                    count(distinct yp.id) as payments_count,
                    count(distinct yp.user_id) as paid_users_count,
                    coalesce(sum(yp.amount), 0) as amount_sum
                from base b
                join yk_payments yp
                  on yp.user_id = b.user_id
                 and yp.status = 'succeeded'
                 and b.sent_at is not null
                 and yp.created_at > b.sent_at
                group by b.id
            )
            select
                d.action_key,
                count(*) as total_count,
                count(*) filter (where d.status = 'pending') as pending_count,
                count(*) filter (where d.status = 'sent') as sent_count,
                count(*) filter (where d.status = 'failed') as failed_count,
                count(distinct d.id) filter (where coalesce(r.reply_count, 0) > 0) as replied_count,
                round(
                    100.0 * count(distinct d.id) filter (where coalesce(r.reply_count, 0) > 0)
                    / nullif(count(*) filter (where d.status = 'sent'), 0),
                    1
                ) as reply_rate,
                count(fr.id) as rewards_count,
                count(fr.id) filter (where fr.status in ('ISSUED', 'issued')) as rewards_issued_count,
                count(fr.id) filter (where fr.status in ('SELECTED', 'selected')) as rewards_selected_count,
                count(fr.id) filter (where fr.status in ('USED', 'used')) as rewards_used_count,
                count(fr.id) filter (where fr.status in ('EXPIRED', 'expired')) as rewards_expired_count,
                round(
                    100.0 * count(fr.id) filter (where fr.status in ('SELECTED', 'selected', 'USED', 'used'))
                    / nullif(count(*) filter (where d.status = 'sent'), 0),
                    1
                ) as reward_select_rate,
                round(
                    100.0 * count(fr.id) filter (where fr.status in ('USED', 'used'))
                    / nullif(count(*) filter (where d.status = 'sent'), 0),
                    1
                ) as reward_purchase_rate,
                count(distinct fr.user_id) filter (where fr.status in ('USED', 'used')) as discount_paid_users_count,
                count(distinct fr.payment_id) filter (where fr.status in ('USED', 'used') and fr.payment_id is not null) as discount_payments_count,
                coalesce(sum(discount_payment.amount) filter (where fr.status in ('USED', 'used')), 0) as discount_revenue,
                coalesce(sum(pa.paid_users_count), 0) as paid_after_count,
                coalesce(sum(pa.payments_count), 0) as payments_after_count,
                coalesce(sum(pa.amount_sum), 0) as revenue_after
            from base d
            left join replies r on r.delivery_id = d.id
            left join feedback_rewards fr on fr.id = d.reward_id
            left join yk_payments discount_payment
              on discount_payment.payment_id = fr.payment_id
             and discount_payment.status = 'succeeded'
            left join paid_after pa on pa.delivery_id = d.id
            group by d.action_key
            order by d.action_key
            """
        ),
        {"days": days, "bot_instance": bot_instance},
    )
    return [dict(row._mapping) for row in result]


async def get_action_control_reward_period_stats(
    session: AsyncSession,
    *,
    days: int = 30,
    bot_instance: str | None = None,
) -> list[dict]:
    result = await session.execute(
        text(
            """
            with base as (
                select
                    d.action_key,
                    nullif(d.trigger_payload->>'feedback_reward_id', '')::bigint as reward_id
                from action_control_deliveries d
                join users u on u.id = d.user_id
                where d.created_at >= now() - (cast(:days as int) * interval '1 day')
                  and (cast(:bot_instance as text) is null or u.bot_instance = cast(:bot_instance as text))
            )
            select
                b.action_key,
                coalesce(fr.selected_subscription_period, '-') as subscription_period,
                count(*) filter (where fr.status in ('SELECTED', 'selected')) as selected_count,
                count(*) filter (where fr.status in ('USED', 'used')) as used_count,
                coalesce(sum(yp.amount) filter (where fr.status in ('USED', 'used')), 0) as revenue
            from base b
            join feedback_rewards fr on fr.id = b.reward_id
            left join yk_payments yp
              on yp.payment_id = fr.payment_id
             and yp.status = 'succeeded'
            where fr.selected_subscription_period is not null
            group by b.action_key, fr.selected_subscription_period
            order by b.action_key, fr.selected_subscription_period
            """
        ),
        {"days": days, "bot_instance": bot_instance},
    )
    return [dict(row._mapping) for row in result]


async def get_recent_action_control_discount_purchases(
    session: AsyncSession,
    *,
    days: int = 30,
    limit: int = 10,
    bot_instance: str | None = None,
) -> list[dict]:
    result = await session.execute(
        text(
            """
            with base as (
                select
                    d.action_key,
                    d.user_id,
                    nullif(d.trigger_payload->>'feedback_reward_id', '')::bigint as reward_id
                from action_control_deliveries d
                join users u on u.id = d.user_id
                where d.created_at >= now() - (cast(:days as int) * interval '1 day')
                  and (cast(:bot_instance as text) is null or u.bot_instance = cast(:bot_instance as text))
            )
            select
                coalesce(fr.used_at, yp.captured_at, yp.created_at) as paid_at,
                b.action_key,
                u.telegram_id,
                u.username,
                fr.selected_subscription_period,
                fr.selected_discount_percent,
                fr.selected_discount_amount,
                yp.amount,
                fr.payment_id
            from base b
            join feedback_rewards fr on fr.id = b.reward_id
            join users u on u.id = fr.user_id
            left join yk_payments yp
              on yp.payment_id = fr.payment_id
             and yp.status = 'succeeded'
            where fr.status in ('USED', 'used')
            order by paid_at desc nulls last
            limit :limit
            """
        ),
        {"days": days, "limit": limit, "bot_instance": bot_instance},
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
            where (cast(:bot_instance as text) is null or u.bot_instance = cast(:bot_instance as text))
            order by r.created_at desc
            limit :limit
            """
        ),
        {"limit": limit, "bot_instance": bot_instance},
    )
    return [dict(row._mapping) for row in result]
