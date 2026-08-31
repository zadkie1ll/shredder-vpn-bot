import os
import string
import tempfile
import unittest
from pathlib import Path

from services.messages_catalog import (
    ACTION_CONTROL_PATH_ENV,
    build_action_control_reward_menu,
    build_messages_catalog,
    build_message_previews,
    load_action_control_messages,
)


class MessagesCatalogTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(ACTION_CONTROL_PATH_ENV, None)

    def test_catalog_contains_every_supported_source(self) -> None:
        document, total = build_messages_catalog()
        html = document.decode("utf-8")

        self.assertGreater(total, 90)
        self.assertIn("Remnawave / Redis уведомления", html)
        self.assertIn("subscription-expired.regular", html)
        self.assertIn("Action Control", html)
        self.assertIn("trial_first_month_offer", html)
        self.assertIn("Feedback-рассылки", html)
        self.assertIn("survey.buttons", html)
        self.assertIn("WELCOME_MESSAGE", html)
        self.assertIn("Спасибо, я передал обратную связь команде.", html)

    def test_action_control_source_can_be_mounted_from_neighbor_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "messages.yml"
            source.write_text("external_message: Привет!\n", encoding="utf-8")
            os.environ[ACTION_CONTROL_PATH_ENV] = str(source)

            path, messages = load_action_control_messages()

        self.assertEqual(path, source)
        self.assertEqual(messages, {"external_message": "Привет!"})

    def test_telegram_previews_include_all_message_sources(self) -> None:
        previews = build_message_previews()
        sources = {source for preview in previews for source in preview.sources}

        self.assertGreater(len(previews), 50)
        self.assertIn("remnawave/subscription-expired.regular", sources)
        self.assertIn("action-control/trial_first_month_offer", sources)
        self.assertIn("feedback/survey.buttons", sources)
        self.assertIn("bot/WELCOME_MESSAGE", sources)
        self.assertTrue(any(preview.buttons for preview in previews))

    def test_previews_have_no_unrendered_format_fields(self) -> None:
        for preview in build_message_previews():
            fields = [
                field
                for _, field, _, _ in string.Formatter().parse(preview.text)
                if field is not None
            ]
            self.assertEqual(fields, [], preview.sources)
            for row in preview.buttons:
                for button in row:
                    button_fields = [
                        field
                        for _, field, _, _ in string.Formatter().parse(button)
                        if field is not None
                    ]
                    self.assertEqual(button_fields, [], preview.sources)

    def test_catalog_excludes_admin_command_responses(self) -> None:
        previews = build_message_previews()
        sources = {source for preview in previews for source in preview.sources}
        texts = "\n".join(preview.text for preview in previews)

        self.assertFalse(any(source.startswith("handlers/service.py") for source in sources))
        self.assertNotIn("Формат: /feedback_send", texts)
        self.assertNotIn("Предпросмотр завершён", texts)
        self.assertIn("Спасибо, я передал обратную связь команде.", texts)
        self.assertNotIn("Нет активного текстового опроса.", texts)

    def test_only_locale_messages_used_by_user_flows_are_included(self) -> None:
        previews = build_message_previews()
        sources = {source for preview in previews for source in preview.sources}

        self.assertIn("bot/WELCOME_MESSAGE", sources)
        self.assertIn("bot/YOUR_PAYMENT", sources)
        self.assertNotIn("bot/INVALID_PRICE", sources)
        self.assertNotIn("bot/PAY_BUTTON_TEXT", sources)

    def test_collapsed_action_message_opens_tariff_menu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "messages.yml"
            source.write_text(
                """
short_feedback_oneday:
  collapse_buttons: true
  text: Держите скидку
  buttons:
    - {text: Забрать скидку, subscription_period: month, discount_percent: 30}
    - {text: Забрать скидку, subscription_period: threemonths, discount_percent: 30}
    - {text: Забрать скидку, subscription_period: sixmonths, discount_percent: 30}
    - {text: Забрать скидку, subscription_period: year, discount_percent: 30}
""".strip(),
                encoding="utf-8",
            )
            os.environ[ACTION_CONTROL_PATH_ENV] = str(source)

            action_preview = next(
                preview
                for preview in build_message_previews()
                if "action-control/short_feedback_oneday" in preview.sources
            )
            reward_menu = build_action_control_reward_menu("short_feedback_oneday")

        self.assertEqual(action_preview.buttons, (("Забрать скидку",),))
        self.assertEqual(
            action_preview.button_callbacks,
            (("messages_action:short_feedback_oneday",),),
        )
        self.assertIsNotNone(reward_menu)
        self.assertEqual(len(reward_menu.buttons), 4)
        self.assertEqual(reward_menu.buttons[0], ("1 месяц - 174₽ (-30%)",))
