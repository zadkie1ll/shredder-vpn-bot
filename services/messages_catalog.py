from __future__ import annotations

import ast
import os
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

import yaml

from texts import feedback_campaigns
from texts.notifications import NOTIFICATION_CONFIG, SILENT_NOTIFICATION_TYPES
from common.models.tariff import (
    OneDayTariff,
    OneMonthTariff,
    OneYearTariff,
    ThreeMonthsTariff,
    TrialPromotionTariff,
)
from utils.encrypt_happ_url import encrypt_happ_url_legacy
from utils.translator import translator

ACTION_CONTROL_PATH_ENV = "MI_VPN_BOT_ACTION_CONTROL_MESSAGES_PATH"
DEFAULT_ACTION_CONTROL_PATH = Path("texts/action_control_messages.yml")
MOUNTED_ACTION_CONTROL_PATH = Path("/app/external/action-control-messages.yml")

MESSAGE_METHODS = {
    "answer",
    "answer_document",
    "answer_photo",
    "edit_text",
    "send_message",
    "send_photo",
}

USER_HANDLER_FILES = {
    Path("handlers/action_control_feedback.py"),
    Path("handlers/cancel_subscription.py"),
    Path("handlers/feedback_campaigns.py"),
    Path("handlers/install.py"),
    Path("handlers/menu.py"),
    Path("handlers/questions.py"),
    Path("handlers/tariffs.py"),
    Path("handlers/technical_work.py"),
}
USER_MIDDLEWARE_FILES = {
    Path("middlewares/throttle.py"),
}
LOCALE_USAGE_FILES = USER_HANDLER_FILES | {
    Path("handlers/buttons.py"),
    Path("handlers/markups.py"),
    Path("middlewares/display_name_restriction.py"),
    Path("texts/notifications.py"),
}
USER_MESSAGE_HELPERS = {
    (Path("handlers/menu.py"), "__maybe_refresh_main_menu_keyboard"),
}

LOCALE_BUTTON_KEYS = {
    "PAY_BUTTON_TEXT",
    "INSTALL_VPN_BUTTON",
    "MY_PROFILE_BUTTON",
    "ANDROID_BUTTON",
    "IOS_BUTTON",
    "WINDOWS_BUTTON",
    "MACOS_BUTTON",
    "PROLONG_BUTTON",
    "TARIFFS_BUTTON",
    "QUESTIONS_BUTTON",
    "INVITE_FRIEND_BUTTON",
    "NO_WL_BUTTON",
    "VPN_DOESNT_WORK_BUTTON",
    "CANCEL_SUBSCRIPTION_BUTTON",
    "BLOCK_ADULT_WEBSITES_BUTTON",
    "SUPPORT_BUTTON",
    "SAVE_RECURRENT_PAYMENT_BUTTON",
    "CANCEL_RECURRENT_PAYMENT_BUTTON",
    "ONE_CLICK_INSTALL_BUTTON",
    "ONE_CLICK_INSTALL_HAPP_BUTTON",
    "ONE_CLICK_INSTALL_INCY_BUTTON",
    "INCY_DOESNT_WORK_BUTTON",
    "BACK_TO_QUESTIONS_BUTTON",
    "THREE_DAYS_PROMO_TARIFF_BUTTON",
    "ONE_DAY_TARIFF_BUTTON",
    "ONE_MONTH_TARIFF_BUTTON",
    "THREE_MONTHS_TARIFF_BUTTON",
    "SIX_MONTHS_TARIFF_BUTTON",
    "ONE_YEAR_TARIFF_BUTTON",
    "SUBSCRIBE_ON_CHANNEL",
}


@dataclass(frozen=True)
class MessagePreview:
    sources: tuple[str, ...]
    text: str
    buttons: tuple[tuple[str, ...], ...] = ()
    button_callbacks: tuple[tuple[str, ...], ...] = ()
    photo: Path | None = None


@dataclass(frozen=True)
class PreviewContext:
    trial_days: int = 7
    referral_days: int = 15
    referrer_days: int = 10
    referral_registration_days: int = 3
    referral_traffic_days: int = 7
    first_name: str = "Алексей"
    invited_count: int = 3
    referral_bonus_total: int = 25
    subscription_url: str = "https://sub.example.com/Ab3x9K"
    payment_url: str = "https://yookassa.ru/checkout/example"
    bot_url: str = "https://t.me/Shredder_vps_bot"

    @classmethod
    def from_config(cls, config: Any | None) -> "PreviewContext":
        if config is None:
            return cls()
        return cls(
            trial_days=config.trial_period_days,
            referral_days=config.referral_bonus_days,
            referrer_days=config.referrer_bonus_days,
            referral_registration_days=config.referral_registration_bonus_days,
            referral_traffic_days=config.referral_traffic_bonus_days,
            bot_url=config.public_bot_url,
        )

    @property
    def custom_json_url(self) -> str:
        return self.subscription_url + "/custom-json"

    @property
    def happ_url(self) -> str:
        return "happ://crypt/" + encrypt_happ_url_legacy(self.custom_json_url)

    @property
    def referral_url(self) -> str:
        return self.bot_url + "?start=a123456789"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"messages catalog source {path} must contain an object")
    return payload


def load_action_control_messages() -> tuple[Path, dict[str, Any]]:
    configured_path = os.getenv(ACTION_CONTROL_PATH_ENV)
    path = Path(configured_path) if configured_path else MOUNTED_ACTION_CONTROL_PATH
    if not path.exists() and DEFAULT_ACTION_CONTROL_PATH.exists():
        path = DEFAULT_ACTION_CONTROL_PATH
    return path, _load_yaml(path)


def _feedback_messages() -> dict[str, str]:
    discount = [
        {
            "reward_type": "discount",
            "subscription_period": "month",
            "discount_percent": 30,
        },
        {"reward_type": "free_days", "days": 7},
    ]
    return {
        "survey.buttons": feedback_campaigns.survey_message("buttons", discount),
        "survey.text": feedback_campaigns.survey_message("text", discount),
        "text_too_short": feedback_campaigns.TEXT_TOO_SHORT.format(
            min_length=60,
            actual_length=24,
        ),
        "missing_location_prompt": feedback_campaigns.MISSING_LOCATION_PROMPT,
        "other_reason_prompt": feedback_campaigns.OTHER_REASON_PROMPT,
        "connection_support_note": feedback_campaigns.CONNECTION_SUPPORT_NOTE,
        "reward_issued": feedback_campaigns.REWARD_ISSUED,
        "free_days_reward_applied": feedback_campaigns.FREE_DAYS_REWARD_APPLIED.format(
            days=7
        ),
    }


def _contains_admin_filter(function: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any("IsAdmin" in ast.unparse(decorator) for decorator in function.decorator_list)


def _is_router_handler(function: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    return any(
        ".message(" in ast.unparse(decorator)
        or ".callback_query(" in ast.unparse(decorator)
        for decorator in function.decorator_list
    )


def _iter_user_message_calls(path: Path, tree: ast.Module):
    if path in USER_MIDDLEWARE_FILES:
        yield from (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        )
        return

    for function in tree.body:
        if not isinstance(function, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        is_helper = (path, function.name) in USER_MESSAGE_HELPERS
        if not is_helper and (
            not _is_router_handler(function) or _contains_admin_filter(function)
        ):
            continue
        yield from (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
        )


def _hardcoded_messages() -> dict[str, str]:
    messages: dict[str, str] = {}
    for path in sorted(USER_HANDLER_FILES | USER_MIDDLEWARE_FILES):
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in _iter_user_message_calls(path, tree):
            if not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in MESSAGE_METHODS:
                continue

            value = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"text", "caption", "error_message"}
                ),
                None,
            )
            if value is None and node.args:
                argument_index = 1 if method in {"send_message", "send_photo"} else 0
                if len(node.args) > argument_index:
                    value = node.args[argument_index]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value
            else:
                continue

            key = f"{path}:{node.lineno}.{method}"
            messages[key] = text
    return messages


def _button_rows(*labels: str) -> tuple[tuple[str, ...], ...]:
    return tuple((label,) for label in labels if label)


def _used_locale_keys(locale: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for path in LOCALE_USAGE_FILES:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr != "get"
                or len(node.args) < 2
                or not isinstance(node.args[0], ast.Constant)
                or node.args[0].value != "ru"
            ):
                continue
            keys.update(
                value.value
                for value in ast.walk(node.args[1])
                if isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value in locale
            )
    return keys


def _locale_format_values(
    key: str,
    context: PreviewContext,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    tariff_prices = {
        "THREE_DAYS_PROMO_TARIFF_BUTTON": TrialPromotionTariff().price,
        "ONE_DAY_TARIFF_BUTTON": OneDayTariff().price,
        "ONE_MONTH_TARIFF_BUTTON": OneMonthTariff().price,
        "THREE_MONTHS_TARIFF_BUTTON": ThreeMonthsTariff().price,
        "SIX_MONTHS_TARIFF_BUTTON": 999,
        "ONE_YEAR_TARIFF_BUTTON": OneYearTariff().price,
    }
    positional: dict[str, tuple[Any, ...]] = {
        "WELCOME_MESSAGE": (context.first_name,),
        "WELCOME_MESSAGE_TRIAL_USER_CREATED": (
            context.first_name,
            context.trial_days,
        ),
        "WELCOME_MESSAGE_REFERRAL": (
            context.first_name,
            context.referral_days,
        ),
        "YOUR_KEY": (context.custom_json_url,),
        "MY_PROFILE": (
            context.custom_json_url,
            context.subscription_url,
            "Активна",
            12.34,
            "31.08.2026",
        ),
        "MY_PROFILE_TRAFFIC_LIMIT": (
            context.custom_json_url,
            context.subscription_url,
            "Активна",
            12.34,
            100.0,
            "Ежемесячно",
            "31.08.2026",
        ),
        "AVAILABLE_THREE_DAYS_PROMO": (TrialPromotionTariff().price,),
        "BLOCK_ADULT_WEBSITES_ANSWER": (context.subscription_url + "/np",),
        "INSTALL_ON_ANDROID_INSTRUCTION": (
            context.happ_url,
            context.custom_json_url,
        ),
        "INSTALL_ON_APPLE_INSTRUCTION": (
            context.happ_url,
            context.custom_json_url,
        ),
        "INSTALL_ON_WINDOWS_INSTRUCTION": (context.subscription_url,),
        "YOUR_PAYMENT": (OneMonthTariff().price, context.payment_url),
        "REFERRAL_PROGRAM": (
            context.referral_registration_days,
            context.referral_traffic_days,
            context.referral_days,
            context.trial_days,
            context.invited_count,
            context.referral_bonus_total,
            context.referral_url,
        ),
        "NOTIFY_REFERRAL_TRAFFIC_REACHED_BONUS": (
            "3 ваших друга использовали",
            context.referral_traffic_days,
        ),
        "NOTIFY_REFERRAL_REGISTRATION_BONUS": (
            context.referral_registration_days,
        ),
        "NOTIFY_REFERRAL_PURCHASE_BONUS_APPLIED": (
            "1 месяц",
            30,
        ),
        "SHARE_REFERRAL_TEXT": (
            context.referral_days,
            context.trial_days,
            context.referral_url,
        ),
    }
    promo_keys = {
        "NOTIFY_EXPIRED_USER_PROMO1",
        "NOTIFY_EXPIRED_USER_PROMO2",
        "NOTIFY_EXPIRED_USER_PROMO3",
        "NOTIFY_EXPIRED_USER_PROMO4",
        "NOTIFY_EXPIRED_USER_PROMO5",
        "NOTIFY_ONE_DAY_LEFT_PROMO",
        "NOTIFY_THREE_DAYS_LEFT_PROMO",
    }
    if key in tariff_prices:
        return (tariff_prices[key],), {}
    if key in promo_keys:
        return (TrialPromotionTariff().price,), {}
    if key == "NOTIFY_SUCCESSFUL_NON_AUTOPAY":
        return (), {"days": 30, "days_word": "дней"}
    return positional.get(key, ()), {}


def _render_locale_text(
    key: str,
    locale: dict[str, Any],
    context: PreviewContext,
) -> str:
    text = translator._replace_public_resource_placeholders(str(locale[key]))
    args, kwargs = _locale_format_values(key, context)
    fields = [
        field
        for _, field, _, _ in string.Formatter().parse(text)
        if field is not None
    ]
    if not fields:
        return text
    if not args and not kwargs:
        raise ValueError(f"No preview values configured for locale message {key}")
    return text.format(*args, **kwargs)


def _notification_text(
    notification_type: str,
    text: str,
    context: PreviewContext,
) -> str:
    if notification_type == "purchase-success-non-autopay" and "{days" in text:
        return text.format(days=30, days_word="дней")
    if notification_type == "referral_traffic_reached_bonus_applied":
        return text.format("3 ваших друга использовали", context.referral_traffic_days)
    if notification_type == "referral_purchase_bonus_applied":
        return text.format("1 месяц", 30)
    if "{}" in text:
        return text.format(TrialPromotionTariff().price)
    return text


def _notification_previews(
    locale: dict[str, Any],
    context: PreviewContext,
) -> list[MessagePreview]:
    previews = []
    tariff_buttons = _button_rows(
        _render_locale_text("ONE_DAY_TARIFF_BUTTON", locale, context),
        _render_locale_text("ONE_MONTH_TARIFF_BUTTON", locale, context),
        _render_locale_text("THREE_MONTHS_TARIFF_BUTTON", locale, context),
        _render_locale_text("ONE_YEAR_TARIFF_BUTTON", locale, context),
    )
    promo_buttons = _button_rows(
        _render_locale_text("THREE_DAYS_PROMO_TARIFF_BUTTON", locale, context),
        _render_locale_text("ONE_MONTH_TARIFF_BUTTON", locale, context),
        _render_locale_text("THREE_MONTHS_TARIFF_BUTTON", locale, context),
        _render_locale_text("ONE_YEAR_TARIFF_BUTTON", locale, context),
    )
    device_buttons = (
        (
            locale.get("ANDROID_BUTTON", "Android"),
            locale.get("IOS_BUTTON", "iOS"),
        ),
        (
            locale.get("WINDOWS_BUTTON", "Windows"),
            locale.get("MACOS_BUTTON", "macOS"),
        ),
    )

    for notification_type, config in NOTIFICATION_CONFIG.items():
        if "static" in config:
            values = [("static", config["static"])]
            if config.get("fallback"):
                values.append(("fallback", config["fallback"]))
        elif "random_list" in config:
            values = [
                (f"random_{index}", text)
                for index, text in enumerate(config["random_list"], start=1)
            ]
        else:
            promo = config.get("promo")
            promo_values = promo if isinstance(promo, list) else [promo]
            values = [
                (f"promo_{index}", text) for index, text in enumerate(promo_values, 1)
            ]
            values.append(("regular", config.get("regular")))

        for variant, text in values:
            buttons = ()
            if notification_type == "nc-yesterday-created":
                buttons = device_buttons
            elif variant.startswith("promo"):
                buttons = promo_buttons
            elif notification_type in {
                "subscription-expired",
                "3-days-left",
                "1-day-left",
            }:
                buttons = tariff_buttons
            previews.append(
                MessagePreview(
                    sources=(f"remnawave/{notification_type}.{variant}",),
                    text=_notification_text(notification_type, str(text), context),
                    buttons=buttons,
                )
            )
    return previews


def _resolve_action_photo(source: Path, photo: str | None) -> Path | None:
    if not photo:
        return None
    photo_path = Path(photo)
    candidates = [source.parent / photo_path, photo_path]
    if source == MOUNTED_ACTION_CONTROL_PATH:
        candidates.insert(
            0,
            source.parent / "action-control-assets" / photo_path.name,
        )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _action_control_previews() -> list[MessagePreview]:
    source, messages = load_action_control_messages()
    previews = []
    for key, value in messages.items():
        if isinstance(value, str):
            previews.append(
                MessagePreview(sources=(f"action-control/{key}",), text=value)
            )
            continue
        if not isinstance(value, dict):
            continue
        buttons_config = value.get("buttons") or []
        if value.get("collapse_buttons") and buttons_config:
            previews.append(
                MessagePreview(
                    sources=(f"action-control/{key}",),
                    text=str(value.get("text", "")),
                    buttons=((str(buttons_config[0].get("text", "Забрать скидку")),),),
                    button_callbacks=((f"messages_action:{key}",),),
                    photo=_resolve_action_photo(source, value.get("photo")),
                )
            )
            continue
        rows: dict[int, list[str]] = {}
        automatic_row = 1000
        for button in buttons_config:
            row = button.get("row")
            if row is None:
                row = automatic_row
                automatic_row += 1
            rows.setdefault(int(row), []).append(str(button.get("text", "")))
        previews.append(
            MessagePreview(
                sources=(f"action-control/{key}",),
                text=str(value.get("text", "")),
                buttons=tuple(tuple(labels) for _, labels in sorted(rows.items())),
                photo=_resolve_action_photo(source, value.get("photo")),
            )
        )
    return previews


def build_action_control_reward_menu(action_key: str) -> MessagePreview | None:
    _, messages = load_action_control_messages()
    value = messages.get(action_key)
    if not isinstance(value, dict) or not value.get("collapse_buttons"):
        return None

    labels = []
    for button in value.get("buttons") or []:
        option = {
            "subscription_period": button.get("subscription_period"),
            "discount_percent": button.get("discount_percent"),
            "discount_amount": button.get("discount_amount"),
        }
        labels.append(feedback_campaigns.reward_button_text(option))
    if not labels:
        return None
    return MessagePreview(
        sources=(f"action-control/{action_key}.reward-menu",),
        text="Выберите тариф со скидкой:",
        buttons=_button_rows(*labels),
    )


def _feedback_previews() -> list[MessagePreview]:
    survey_buttons = _button_rows(
        *(option["text"] for option in feedback_campaigns.SURVEY_BUTTON_OPTIONS)
    )
    previews = []
    reward_buttons = _button_rows(
        feedback_campaigns.reward_button_text(
            {
                "subscription_period": "month",
                "discount_percent": 30,
            }
        ),
        feedback_campaigns.free_days_reward_button_text({"days": 7}),
    )
    for key, text in _feedback_messages().items():
        buttons = survey_buttons if key == "survey.buttons" else ()
        if key == "reward_issued":
            buttons = reward_buttons
        elif key == "connection_support_note":
            buttons = _button_rows(feedback_campaigns.CONNECTION_SUPPORT_BUTTON)
        previews.append(
            MessagePreview(
                sources=(f"feedback/{key}",),
                text=text,
                buttons=buttons,
            )
        )
    return previews


def _locale_buttons(
    key: str,
    locale: dict[str, Any],
    context: PreviewContext,
) -> tuple[tuple[str, ...], ...]:
    def label(button_key: str, fallback: str) -> str:
        if button_key not in locale:
            return fallback
        return _render_locale_text(button_key, locale, context)

    if key.startswith("WELCOME_MESSAGE"):
        return (
            (
                label("INSTALL_VPN_BUTTON", "Установить"),
                label("MY_PROFILE_BUTTON", "Профиль"),
            ),
            (
                label("TARIFFS_BUTTON", "Тарифы"),
                label("QUESTIONS_BUTTON", "Вопросы"),
            ),
            (label("INVITE_FRIEND_BUTTON", "Пригласить друга"),),
        )
    if key == "SELECT_YOUR_DEVICE":
        return (
            (label("ANDROID_BUTTON", "Android"), label("IOS_BUTTON", "iOS")),
            (
                label("WINDOWS_BUTTON", "Windows"),
                label("MACOS_BUTTON", "macOS"),
            ),
        )
    if key in {"SELECT_TARIFF", "RENEW"}:
        return _button_rows(
            label("ONE_DAY_TARIFF_BUTTON", "1 день"),
            label("ONE_MONTH_TARIFF_BUTTON", "1 месяц"),
            label("THREE_MONTHS_TARIFF_BUTTON", "3 месяца"),
            label("ONE_YEAR_TARIFF_BUTTON", "1 год"),
        )
    if key == "QUESTIONS":
        return _button_rows(
            locale.get("NO_WL_BUTTON", "Белые списки"),
            locale.get("VPN_DOESNT_WORK_BUTTON", "Не работает"),
            locale.get("CANCEL_SUBSCRIPTION_BUTTON", "Отмена подписки"),
            locale.get("SUPPORT_BUTTON", "Поддержка"),
        )
    if key == "CANCEL_AUTOPAY_OBJECTION":
        return (
            (
                locale.get("SAVE_RECURRENT_PAYMENT_BUTTON", "Оставить"),
                locale.get("CANCEL_RECURRENT_PAYMENT_BUTTON", "Отключить"),
            ),
        )
    if key.startswith("MY_PROFILE"):
        return _button_rows(label("SUBSCRIBE_ON_CHANNEL", "Подписаться на канал"))
    if key in {"INSTALL_ON_ANDROID_INSTRUCTION", "INSTALL_ON_APPLE_INSTRUCTION"}:
        return _button_rows(
            label("ONE_CLICK_INSTALL_INCY_BUTTON", "Подключиться через INCY"),
            label("INCY_DOESNT_WORK_BUTTON", "Не работает INCY"),
        )
    if key == "INSTALL_ON_WINDOWS_INSTRUCTION":
        return _button_rows(
            label("ONE_CLICK_INSTALL_BUTTON", "Подключиться в 1 клик!")
        )
    if key == "REFERRAL_PROGRAM":
        return _button_rows("Поделиться ссылкой")
    return ()


def build_message_previews(config: Any | None = None) -> list[MessagePreview]:
    locale = translator.translations.get("ru", {})
    context = PreviewContext.from_config(config)
    notification_locale_keys = {
        key
        for key in locale
        if key.startswith("NOTIFY_")
        and key != "NOTIFY_REFERRAL_REGISTRATION_BONUS"
    }
    used_locale_keys = _used_locale_keys(locale)
    previews = [
        *_notification_previews(locale, context),
        *_action_control_previews(),
        *_feedback_previews(),
    ]

    for key, text in locale.items():
        if (
            key not in used_locale_keys
            or key in LOCALE_BUTTON_KEYS
            or key in notification_locale_keys
        ):
            continue
        previews.append(
            MessagePreview(
                sources=(f"bot/{key}",),
                text=_render_locale_text(key, locale, context),
                buttons=_locale_buttons(key, locale, context),
            )
        )
    for key, text in _hardcoded_messages().items():
        previews.append(MessagePreview(sources=(str(key),), text=text))

    deduplicated: dict[tuple[Any, ...], MessagePreview] = {}
    for preview in previews:
        identity = (
            preview.text.strip(),
            preview.buttons,
            preview.button_callbacks,
            preview.photo,
        )
        existing = deduplicated.get(identity)
        if existing is None:
            deduplicated[identity] = preview
        else:
            deduplicated[identity] = MessagePreview(
                sources=existing.sources + preview.sources,
                text=existing.text,
                buttons=existing.buttons,
                button_callbacks=existing.button_callbacks,
                photo=existing.photo,
            )
    return list(deduplicated.values())


def _entry(key: str, text: Any, metadata: list[str] | None = None) -> str:
    details = "".join(f"<li>{escape(item)}</li>" for item in metadata or [])
    details_html = f"<ul>{details}</ul>" if details else ""
    return (
        '<article class="message">'
        f"<h3>{escape(key)}</h3>{details_html}"
        f"<pre>{escape(str(text))}</pre>"
        "</article>"
    )


def _action_control_entries(messages: dict[str, Any]) -> str:
    result = []
    for key, value in messages.items():
        if isinstance(value, str):
            result.append(_entry(key, value))
            continue

        if not isinstance(value, dict):
            result.append(_entry(key, value))
            continue

        metadata = []
        if value.get("photo"):
            metadata.append(f"Фото: {value['photo']}")
        if value.get("reward_expires_hours"):
            metadata.append(f"Награда действует: {value['reward_expires_hours']} ч.")
        for button in value.get("buttons") or []:
            reward = ""
            if button.get("discount_percent") is not None:
                reward = f", скидка {button['discount_percent']}%"
            elif button.get("discount_amount") is not None:
                reward = f", скидка {button['discount_amount']}₽"
            metadata.append(
                f"Кнопка: {button.get('text')} → {button.get('subscription_period')}"
                f"{reward}"
            )
        result.append(_entry(key, value.get("text", ""), metadata))
    return "".join(result)


def _notification_entries(locale: dict[str, Any]) -> str:
    result = []
    for notification_type, config in NOTIFICATION_CONFIG.items():
        metadata = [f"notification_type: {notification_type}"]
        if notification_type in SILENT_NOTIFICATION_TYPES:
            metadata.append("Не отправляется пользователю")

        if "static" in config:
            values = [("static", config["static"])]
            if config.get("fallback"):
                values.append(("fallback", config["fallback"]))
        elif "random_list" in config:
            values = [
                (f"random_{index}", text)
                for index, text in enumerate(config["random_list"], start=1)
            ]
        else:
            promo = config.get("promo")
            promo_values = promo if isinstance(promo, list) else [promo]
            values = [
                (f"promo_{index}", text) for index, text in enumerate(promo_values, 1)
            ]
            values.append(("regular", config.get("regular")))

        for variant, value in values:
            result.append(_entry(f"{notification_type}.{variant}", value, metadata))

    # Failure notifications are intentionally silent today, but their copy remains
    # part of the catalog so an admin can see every configured user-facing template.
    for key in ("NOTIFY_AUTOPAY_FAILURE", "NOTIFY_NON_AUTOPAY_FAILURE"):
        if key in locale:
            result.append(
                _entry(
                    key, locale[key], ["Настроено в локали, сейчас отправка отключена"]
                )
            )
    return "".join(result)


def _notification_variant_count(locale: dict[str, Any]) -> int:
    count = 0
    for config in NOTIFICATION_CONFIG.values():
        if "static" in config:
            count += 1 + int(bool(config.get("fallback")))
        elif "random_list" in config:
            count += len(config["random_list"])
        else:
            promo = config.get("promo")
            count += len(promo) if isinstance(promo, list) else 1
            count += 1
    count += sum(
        key in locale
        for key in ("NOTIFY_AUTOPAY_FAILURE", "NOTIFY_NON_AUTOPAY_FAILURE")
    )
    return count


def build_messages_catalog() -> tuple[bytes, int]:
    locale = translator.translations.get("ru", {})
    action_path, action_messages = load_action_control_messages()
    feedback_messages = _feedback_messages()
    hardcoded_messages = _hardcoded_messages()

    locale_entries = "".join(_entry(key, value) for key, value in locale.items())
    hardcoded_entries = "".join(
        _entry(key, value) for key, value in hardcoded_messages.items()
    )
    feedback_entries = "".join(
        _entry(key, value) for key, value in feedback_messages.items()
    )
    notification_entries = _notification_entries(locale)
    action_entries = _action_control_entries(action_messages)

    total = (
        len(locale)
        + len(hardcoded_messages)
        + len(feedback_messages)
        + _notification_variant_count(locale)
        + len(action_messages)
    )
    generated_at = datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC")
    source = escape(str(action_path))
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Каталог сообщений Shredder</title>
  <style>
    body {{ max-width: 980px; margin: 32px auto; padding: 0 18px; font: 15px/1.45 system-ui, sans-serif; color: #18212b; }}
    h1, h2, h3 {{ line-height: 1.2; }}
    h2 {{ margin-top: 42px; border-bottom: 2px solid #d8dee6; padding-bottom: 8px; }}
    h3 {{ font-family: ui-monospace, monospace; font-size: 14px; }}
    .message {{ border: 1px solid #d8dee6; border-radius: 10px; margin: 14px 0; padding: 12px 16px; break-inside: avoid; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #f6f8fa; padding: 12px; border-radius: 6px; }}
    .note {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>Все пользовательские сообщения Shredder</h1>
  <p class="note">Сформировано {generated_at}. Переменные вида <code>{{}}</code> заполняются данными пользователя, тарифа или платежа.</p>
  <h2>Remnawave / Redis уведомления</h2>
  {notification_entries}
  <h2>Action Control</h2>
  <p class="note">Источник: <code>{source}</code></p>
  {action_entries}
  <h2>Feedback-рассылки</h2>
  {feedback_entries}
  <h2>Сообщения интерфейса бота и подписи кнопок</h2>
  {locale_entries}
  <h2>Сообщения, заданные непосредственно в обработчиках</h2>
  {hardcoded_entries}
</body>
</html>"""
    return html.encode("utf-8"), total
