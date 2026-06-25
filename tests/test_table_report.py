from collections import defaultdict
from datetime import date
from datetime import datetime
from unittest import TestCase

from handlers.service import generate_table_report_messages
from handlers.service import generate_trial_conversion_report_messages
from handlers.service import register_first_paid_tariff


class GenerateTableReportMessagesTest(TestCase):
    def test_adds_tariff_breakdown_for_each_source(self):
        report_data = {
            "daily_stats": {
                date(2026, 5, 1): {
                    "entered_bot_user_ids": set(),
                    "connected_user_ids": set(),
                    "paid_user_ids": set(),
                    "payments_count": 0,
                    "payments_sum": 0,
                    "tariff_stats": {},
                    "not_renewed_user_ids": set(),
                }
            },
            "source_rows": [
                {
                    "source": "TS_201",
                    "new_users": 100,
                    "paid_users": 12,
                    "tariff_stats": {
                        "1 год": 2,
                        "1 месяц": 8,
                        "1 день": 2,
                    },
                }
            ],
        }

        messages = generate_table_report_messages(
            report_data=report_data,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            trial_period_days=3,
        )

        self.assertIn("TS_201 | 100 | 12", messages[-1])
        self.assertIn(
            "Тариф перехода: 1 день: 2 | 1 месяц: 8 | 1 год: 2",
            messages[-1],
        )

    def test_counts_only_first_paid_tariff_for_user(self):
        source_stat = {
            "paid_users": set(),
            "tariff_users": defaultdict(set),
        }

        register_first_paid_tariff(source_stat, 101, "threedays")
        register_first_paid_tariff(source_stat, 101, "month")
        register_first_paid_tariff(source_stat, 102, "month")

        self.assertEqual(source_stat["paid_users"], {101, 102})
        self.assertEqual(source_stat["tariff_users"]["3 дня"], {101})
        self.assertEqual(source_stat["tariff_users"]["1 месяц"], {102})


class GenerateTrialConversionReportMessagesTest(TestCase):
    def test_generates_summary_and_converted_users(self):
        report_data = {
            "trial_users_count": 4,
            "converted_users": [
                {
                    "telegram_id": 123456,
                    "telegram_username": "turtle",
                    "trial_purchased_at": datetime(2026, 5, 1, 10, 0),
                    "next_payment_at": datetime(2026, 5, 4, 12, 30),
                    "next_tariff": "month",
                    "next_payment_amount": 249,
                }
            ],
            "tariff_stats": {"1 месяц": 1},
        }

        messages = generate_trial_conversion_report_messages(
            report_data=report_data,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 31),
        )
        report = "\n".join(messages)

        self.assertIn("Купили тариф на 3 дня: <b>4</b>", report)
        self.assertIn("Купили другой тариф позже: <b>1</b>", report)
        self.assertIn("Конверсия: <b>25.0%</b>", report)
        self.assertIn("<code>123456</code> (@turtle)", report)
        self.assertIn("затем: 1 месяц, 249 ₽", report)

    def test_handles_empty_report(self):
        messages = generate_trial_conversion_report_messages(
            report_data={
                "trial_users_count": 0,
                "converted_users": [],
                "tariff_stats": {},
            },
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 31),
        )

        self.assertIn("Конверсия: <b>0.0%</b>", messages[0])
        self.assertIn("Конверсий за выбранный период нет", messages[0])
