from datetime import date
from unittest import TestCase

from handlers.service import generate_table_report_messages


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
            "Тарифы: 1 день: 2 | 1 месяц: 8 | 1 год: 2",
            messages[-1],
        )
