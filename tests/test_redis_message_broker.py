from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from unittest.mock import patch

from redis.exceptions import TimeoutError as RedisTimeoutError

from utils.redis_message_broker import RedisMessageBroker


class RedisMessageBrokerTest(IsolatedAsyncioTestCase):
    @patch("utils.redis_message_broker.Redis")
    async def test_pop_message_treats_blocking_timeout_as_empty_queue(self, redis_cls):
        redis = redis_cls.return_value
        redis.blpop = AsyncMock(side_effect=RedisTimeoutError("read timed out"))
        config = SimpleNamespace(
            redis_host="localhost",
            redis_port=6379,
            redis_password="password",
            redis_queue_name="notifications",
        )
        broker = RedisMessageBroker(config)

        message = await broker.pop_message(timeout=5)

        self.assertIsNone(message)
        redis.blpop.assert_awaited_once_with("notifications", timeout=5)
