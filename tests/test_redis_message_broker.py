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

    @patch("utils.redis_message_broker.Redis")
    async def test_increment_notification_report_stat(self, redis_cls):
        redis = redis_cls.return_value
        redis.hincrby = AsyncMock()
        redis.expire = AsyncMock()
        config = SimpleNamespace(
            redis_host="localhost",
            redis_port=6379,
            redis_password="password",
            redis_queue_name="notifications",
        )
        broker = RedisMessageBroker(config)

        await broker.increment_notification_report_stat(
            "primary",
            "subscription-expired",
            "sent",
        )

        key = "notification-delivery-report:primary"
        redis.hincrby.assert_any_await(key, "total", 1)
        redis.hincrby.assert_any_await(key, "status:sent", 1)
        redis.hincrby.assert_any_await(key, "type:subscription-expired", 1)
        redis.hincrby.assert_any_await(
            key,
            "type_status:subscription-expired:sent",
            1,
        )
        redis.expire.assert_awaited_once_with(key, 7 * 24 * 60 * 60)

    @patch("utils.redis_message_broker.Redis")
    async def test_pop_notification_report_stats_deletes_existing_stats(
        self, redis_cls
    ):
        redis = redis_cls.return_value
        redis.hgetall = AsyncMock(return_value={"total": "2", "status:sent": "2"})
        redis.delete = AsyncMock()
        config = SimpleNamespace(
            redis_host="localhost",
            redis_port=6379,
            redis_password="password",
            redis_queue_name="notifications",
        )
        broker = RedisMessageBroker(config)

        stats = await broker.pop_notification_report_stats("primary")

        self.assertEqual(stats, {"total": 2, "status:sent": 2})
        redis.delete.assert_awaited_once_with("notification-delivery-report:primary")

    @patch("utils.redis_message_broker.Redis")
    async def test_get_notification_report_stats_does_not_delete_stats(
        self, redis_cls
    ):
        redis = redis_cls.return_value
        redis.hgetall = AsyncMock(return_value={"total": "2", "status:sent": "2"})
        redis.delete = AsyncMock()
        config = SimpleNamespace(
            redis_host="localhost",
            redis_port=6379,
            redis_password="password",
            redis_queue_name="notifications",
        )
        broker = RedisMessageBroker(config)

        stats = await broker.get_notification_report_stats("primary")

        self.assertEqual(stats, {"total": 2, "status:sent": 2})
        redis.delete.assert_not_awaited()
