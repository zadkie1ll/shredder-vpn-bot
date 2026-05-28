from common.models.tariff import str_to_tariff
from common.models.tariff import tariff_to_human_str

DEFAULT_DISCOUNT_PERCENT = 30
MIN_DISCOUNT_PERCENT = 1
MAX_DISCOUNT_PERCENT = 95
DEFAULT_MIN_TEXT_LENGTH = 60
DEFAULT_REWARD_EXPIRES_DAYS = 14
CONNECTION_PROBLEM_BUTTON_VALUE = 2
MISSING_LOCATION_BUTTON_VALUE = 4
OTHER_REASON_BUTTON_VALUE = 5

SURVEY_BUTTON_OPTIONS = [
    {"value": 1, "text": "Не устроила цена"},
    {"value": 2, "text": "Не разобрался с подключением"},
    {"value": 3, "text": "Не хватило скорости"},
    {"value": 4, "text": "Не было нужной локации"},
    {"value": 5, "text": "Другая причина"},
]

SURVEY_MESSAGES = {
    "buttons": (
        "Нам нужен твой совет! Расскажи, что тебе не понравилось в нашем VPN "
        "и что нам нужно улучшить, чтобы ты вернулся к нам. "
        "За честный ответ - честная скидка.\n\n"
        "Выбери один из вариантов ниже."
    ),
    "text": (
        "Нам нужен твой совет! Расскажи, что тебе не понравилось в нашем VPN "
        "и что нам нужно улучшить, чтобы ты вернулся к нам. "
        "За честный ответ - честная скидка.\n\n"
        "Напиши ответ одним сообщением."
    ),
}

TEXT_TOO_SHORT = (
    "Спасибо! Ответ пока коротковат: нужно минимум {min_length} символов, "
    "сейчас {actual_length}. Напиши чуть подробнее, пожалуйста."
)

MISSING_LOCATION_PROMPT = (
    "Спасибо! Напиши, пожалуйста, какой страны тебе не хватило. "
    "После ответа, дадим приятный бонус."
)
OTHER_REASON_PROMPT = (
    "Спасибо! Напиши, пожалуйста, что именно нам нужно улучшить. "
    "После ответа, дадим приятный бонус."
)
CONNECTION_SUPPORT_NOTE = (
    "Если проблема с подключением все еще актуальна, напиши в поддержку:\n"
    "{support_text}\n\n"
    "А награду за ответ мы все равно уже подготовили."
)
REWARD_ISSUED = "Спасибо за честный ответ! Вот твоя награда"
FREE_DAYS_REWARD_APPLIED = "Готово! Мы добавили к твоей подписке {days} дн."

NO_PENDING_TEXT_SURVEY = "Нет активного текстового опроса."


def reward_button_text(option: dict) -> str:
    tariff = str_to_tariff(option["subscription_period"])
    original_price = tariff.price
    discount_percent = option.get("discount_percent") or 0
    discount_amount = option.get("discount_amount") or 0

    if discount_percent:
        price = round(original_price * (100 - discount_percent) / 100)
        discount_label = f"-{discount_percent}%"
    else:
        price = max(1, original_price - discount_amount)
        discount_label = f"-{discount_amount}₽"

    return f"{tariff_to_human_str(tariff)} - {price}₽ ({discount_label})"


def free_days_reward_button_text(option: dict) -> str:
    return f"+{option['days']} дн. бесплатно"
