import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime
from zoneinfo import ZoneInfo

from common.models.messages import NotificateUserMessage
from utils.notifications import has_telegram_recipient
from utils.notifications import send_daily_notification_report
import handlers.service as service
from utils.notification_reports import format_notification_report
from utils.notification_reports import seconds_until_next_daily_report


class NotificationRecipientTest(unittest.TestCase):
    def test_accepts_positive_telegram_id(self):
        message = NotificateUserMessage(
            notification_type="purchase-success-non-autopay",
            telegram_id=123,
        )

        self.assertTrue(has_telegram_recipient(message))

    def test_rejects_missing_telegram_id(self):
        message = NotificateUserMessage(
            notification_type="purchase-success-non-autopay",
        )

        self.assertFalse(has_telegram_recipient(message))

    def test_rejects_legacy_synthetic_telegram_id(self):
        message = NotificateUserMessage(
            notification_type="purchase-success-non-autopay",
            telegram_id=-123,
        )

        self.assertFalse(has_telegram_recipient(message))


class NotificationReportDestinationTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_report_only_goes_to_owner_even_without_admins(self):
        for admins in ([], [111, 222, 1297686797]):
            bot = SimpleNamespace(send_message=AsyncMock())
            broker = SimpleNamespace(pop_notification_report_stats=AsyncMock(return_value={}))
            config = SimpleNamespace(admins=admins, bot_instance_id="secondary")
            await send_daily_notification_report(bot, broker, config)
            bot.send_message.assert_awaited_once()
            self.assertEqual(bot.send_message.call_args.kwargs["chat_id"], 1297686797)

    async def test_manual_report_does_not_reply_with_stats_in_requesting_chat(self):
        message = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), answer=AsyncMock())
        broker = SimpleNamespace(get_notification_report_stats=AsyncMock(return_value={}))
        await getattr(service, "__on_notifications_report_requested")(
            message, broker, SimpleNamespace(bot_instance_id="secondary")
        )
        message.bot.send_message.assert_awaited_once()
        self.assertEqual(message.bot.send_message.call_args.kwargs["chat_id"], 1297686797)
        message.answer.assert_not_awaited()


class DailyNotificationReportTest(unittest.TestCase):
    def test_seconds_until_next_daily_report_today(self):
        now = datetime(2026, 9, 7, 18, 30, tzinfo=ZoneInfo("Europe/Moscow"))

        self.assertEqual(seconds_until_next_daily_report(now), 30 * 60)

    def test_seconds_until_next_daily_report_tomorrow(self):
        now = datetime(2026, 9, 7, 19, 1, tzinfo=ZoneInfo("Europe/Moscow"))

        self.assertEqual(seconds_until_next_daily_report(now), 23 * 60 * 60 + 59 * 60)

    def test_format_notification_report(self):
        report = format_notification_report(
            {
                "total": 4,
                "status:sent": 2,
                "status:undeliverable": 1,
                "status:failed": 1,
                "type:subscription-expired": 3,
                "type:1-day-left": 1,
            },
            "primary",
        )

        self.assertIn("Бот: <code>primary</code>", report)
        self.assertIn("Всего обработано: <b>4</b>", report)
        self.assertIn("Отправлено: <b>2</b>", report)
        self.assertIn("<code>subscription-expired</code>: 3", report)


if __name__ == "__main__":
    unittest.main()
