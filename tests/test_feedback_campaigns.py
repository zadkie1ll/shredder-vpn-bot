from datetime import datetime
from datetime import timedelta
from types import SimpleNamespace
from unittest import TestCase

from repositories.feedback_campaigns import find_matching_feedback_payment


class FindMatchingFeedbackPaymentTest(TestCase):
    def setUp(self):
        self.issued_at = datetime(2026, 5, 28, 9, 29)
        self.reward = SimpleNamespace(
            selected_subscription_period="month",
            selected_discount_percent=70,
            selected_discount_amount=None,
            issued_at=self.issued_at,
            expires_at=self.issued_at + timedelta(days=30),
        )

    def payment(self, **overrides):
        values = {
            "payment_id": "payment-1",
            "currency": "RUB",
            "subscription_period": "month",
            "amount": 75,
            "captured_at": datetime(2026, 5, 31, 11, 48),
            "created_at": datetime(2026, 5, 31, 11, 46),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_matches_succeeded_discount_payment_record(self):
        payment = self.payment()

        result = find_matching_feedback_payment(self.reward, [payment], set())

        self.assertIs(result, payment)

    def test_rejects_regular_price_and_already_assigned_payment(self):
        regular_payment = self.payment(amount=249)
        assigned_payment = self.payment(payment_id="assigned")

        result = find_matching_feedback_payment(
            self.reward,
            [regular_payment, assigned_payment],
            {"assigned"},
        )

        self.assertIsNone(result)

    def test_rejects_payment_outside_reward_window(self):
        expired_payment = self.payment(
            captured_at=self.reward.expires_at + timedelta(seconds=1)
        )

        result = find_matching_feedback_payment(
            self.reward,
            [expired_payment],
            set(),
        )

        self.assertIsNone(result)
