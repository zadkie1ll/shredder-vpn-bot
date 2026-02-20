from common.models.tariff import (
    TrialPromotionTariff,
    OneDayTariff,
    OneMonthTariff,
    ThreeMonthsTariff,
    SixMonthsTariff,
    OneYearTariff,
)

from utils.translator import translator as ts

THREE_DAYS_PROMO_TARIFF_BUTTON = ts.get(
    "ru", "THREE_DAYS_PROMO_TARIFF_BUTTON", TrialPromotionTariff().price
)
ONE_DAY_TARIFF_BUTTON = ts.get("ru", "ONE_DAY_TARIFF_BUTTON", OneDayTariff().price)
ONE_MONTH_TARIFF_BUTTON = ts.get(
    "ru", "ONE_MONTH_TARIFF_BUTTON", OneMonthTariff().price
)
THREE_MONTHS_TARIFF_BUTTON = ts.get(
    "ru", "THREE_MONTHS_TARIFF_BUTTON", ThreeMonthsTariff().price
)
SIX_MONTHS_TARIFF_BUTTON = ts.get(
    "ru", "SIX_MONTHS_TARIFF_BUTTON", SixMonthsTariff().price
)
ONE_YEAR_TARIFF_BUTTON = ts.get("ru", "ONE_YEAR_TARIFF_BUTTON", OneYearTariff().price)

BACK_TO_QUESTIONS_BUTTON_DATA = "back_to_questions"
