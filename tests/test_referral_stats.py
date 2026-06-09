from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase

from handlers.service import generate_user_referrals_messages


class GenerateUserReferralMessagesTest(TestCase):
    def test_generates_full_referral_profile(self):
        referrer = SimpleNamespace(
            telegram_id=100,
            telegram_username="owner",
            username="owner_internal",
            referral_type=None,
        )
        invited_by = SimpleNamespace(
            telegram_id=50,
            telegram_username="parent",
        )
        referral = SimpleNamespace(
            telegram_id=200,
            telegram_username="friend",
        )
        report_data = {
            "referrer": referrer,
            "invited_by": invited_by,
            "link": SimpleNamespace(created_at=datetime(2026, 6, 1)),
            "referrals": [
                {
                    "user": referral,
                    "first_seen": datetime(2026, 6, 2),
                    "payments_count": 2,
                    "payments_sum": 498,
                    "bonus_days": 30,
                    "referral_type": "Стандартная",
                }
            ],
            "paid_count": 1,
            "payments_count": 2,
            "payments_sum": 498,
            "bonus_days": 30,
            "bonus_stats": {"За покупку": 30},
            "referral_type_stats": {"Стандартная": 1},
            "tariff_stats": {
                "1 месяц": {
                    "payments_count": 2,
                    "payments_sum": 498,
                }
            },
        }

        messages = generate_user_referrals_messages(report_data)
        report = "\n".join(messages)

        self.assertIn("РЕФЕРАЛЬНАЯ СТАТИСТИКА", report)
        self.assertIn("Всего приглашено: <b>1</b>", report)
        self.assertIn("Перешли на платный: <b>1</b> (100.0%)", report)
        self.assertIn("Успешных оплат: <b>2</b>", report)
        self.assertIn("Сумма оплат рефералов: <b>498 ₽</b>", report)
        self.assertIn("?start=sowner_internal", report)
        self.assertIn("- 1 месяц: <b>2</b> / <b>498 ₽</b>", report)
        self.assertIn("Стандартная | 2 оплат / 498 ₽", report)
