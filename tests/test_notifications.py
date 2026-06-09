import unittest

from common.models.messages import NotificateUserMessage
from utils.notifications import has_telegram_recipient


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


if __name__ == "__main__":
    unittest.main()
