import orjson
import logging

from utils.config import Config
from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from typing import Type
from pydantic import ValidationError

from common.models.messages import (
    BaseMessage,
    NotificateUserMessage,
    ReferralPurchaseBonusApplied,
    SendConversionMessage,
    SendPurchaseMessage,
    ReferralReachedTrafficBonusApplied,
    MessageUnion,
)


class RedisMessageBroker:
    def __init__(self, config: Config):
        self.__redis = Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            decode_responses=True,
        )

        self.__config = config

        logging.info(f"connected to Redis at {config.redis_host}:{config.redis_port}")

        self.__message_type_mapping: dict[str, Type[BaseMessage]] = {
            "send-conversion": SendConversionMessage,
            "send-purchase": SendPurchaseMessage,
            "notificate-user": NotificateUserMessage,
            "standard-ref-referral-traffic-reached": ReferralReachedTrafficBonusApplied,
            "standard-ref-referral-purchase": ReferralPurchaseBonusApplied,
        }

    async def push_message_to_ym_stat(self, message: MessageUnion):
        try:
            data = message.model_dump()
            json = orjson.dumps(data).decode("utf-8")
            await self.__redis.rpush("monkey-island-ym-stat", json)
            logging.debug(f"pushed message of type {data.get('type')} to Redis")
        except Exception:
            logging.exception(
                f"failed to push message to Redis, content: {message.model_dump_json()}"
            )

    async def requeue_message(self, message: MessageUnion):
        try:
            json = message.model_dump_json()
            await self.__redis.rpush(self.__config.redis_queue_name, json)
            logging.debug(
                "requeued message of type %s to %s",
                getattr(message, "type", type(message).__name__),
                self.__config.redis_queue_name,
            )
        except Exception:
            logging.exception(
                "failed to requeue message, content: %s",
                message.model_dump_json(),
            )

    async def remember_pending_conversion(
        self, telegram_id: int, event_id: int, ttl_seconds: int
    ) -> None:
        """Запоминает, что этому юзеру отправлено 'предложение купить' (event_id
        в vpn-bot-admin) — чтобы потом, если он оплатит, отметить конверсию.
        TTL нужен, чтобы не копить мусор для юзеров, которые так и не купили."""
        try:
            await self.__redis.set(
                f"admin-pending-conversion:{telegram_id}", str(event_id), ex=ttl_seconds
            )
        except Exception:
            logging.exception(
                "failed to remember pending conversion for telegram_id=%s", telegram_id
            )

    async def pop_pending_conversion(self, telegram_id: int) -> int | None:
        """Забирает и удаляет запомненное 'предложение купить' для юзера, если есть."""
        key = f"admin-pending-conversion:{telegram_id}"
        try:
            value = await self.__redis.get(key)
            if value is None:
                return None
            await self.__redis.delete(key)
            return int(value)
        except Exception:
            logging.exception(
                "failed to pop pending conversion for telegram_id=%s", telegram_id
            )
            return None

    async def increment_notification_report_stat(
        self,
        bot_instance_id: str,
        notification_type: str,
        status: str,
    ) -> None:
        key = f"notification-delivery-report:{bot_instance_id}"
        try:
            await self.__redis.hincrby(key, "total", 1)
            await self.__redis.hincrby(key, f"status:{status}", 1)
            await self.__redis.hincrby(key, f"type:{notification_type}", 1)
            await self.__redis.hincrby(
                key, f"type_status:{notification_type}:{status}", 1
            )
            await self.__redis.expire(key, 7 * 24 * 60 * 60)
        except Exception:
            logging.exception(
                "failed to increment notification report stats for bot_instance=%s",
                bot_instance_id,
            )

    async def pop_notification_report_stats(
        self,
        bot_instance_id: str,
    ) -> dict[str, int]:
        key = f"notification-delivery-report:{bot_instance_id}"
        try:
            raw_stats = await self.__redis.hgetall(key)
            if raw_stats:
                await self.__redis.delete(key)
            return {field: int(value) for field, value in raw_stats.items()}
        except Exception:
            logging.exception(
                "failed to pop notification report stats for bot_instance=%s",
                bot_instance_id,
            )
            return {}

    async def pop_message(self, timeout: int) -> MessageUnion | None:
        try:
            job = await self.__redis.blpop(
                self.__config.redis_queue_name,
                timeout=timeout,
            )
        except RedisTimeoutError:
            logging.debug("redis queue wait timed out without a message")
            return None

        if job is None:
            logging.debug("no messages in a queue, waiting...")
            return None

        _, data = job

        try:
            message_data = orjson.loads(data)
            message_type = message_data.get("type")

            if message_type not in self.__message_type_mapping:
                logging.warning(f"unknown message type received: {message_type}")
                return None

            message_cls = self.__message_type_mapping[message_type]
            message = message_cls.model_validate(message_data)

            logging.debug(f"popped message of type {message_type} from Redis")
            return message

        except ValidationError as e:
            logging.error(f"invalid message data: {e}")
            return None
        except AttributeError as e:
            logging.error(f"error processing message: {e}")
            return None
        except Exception:
            logging.exception("unexpected error while popping message from Redis")
            return None
