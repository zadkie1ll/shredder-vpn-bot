import orjson
import logging

from utils.config import Config
from redis.asyncio import Redis
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

    async def pop_message(self, timeout: int) -> MessageUnion:
        job = await self.__redis.blpop(self.__config.redis_queue_name, timeout=timeout)

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
