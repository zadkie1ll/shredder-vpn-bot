from utils.translator import translator as ts

SUB_EXPIRED_PROMO_MESSAGES = [
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO1"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO2"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO3"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO4"),
    ts.get("ru", "NOTIFY_EXPIRED_USER_PROMO5"),
]

NOT_CONNECTED_MESSAGES = [
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED1"),
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED2"),
    ts.get("ru", "NOTIFY_YESTERDAY_CREATED3"),
]

NOTIFICATION_CONFIG = {
    "subscription-expired": {
        "promo": SUB_EXPIRED_PROMO_MESSAGES,
        "regular": ts.get("ru", "NOTIFY_EXPIRED_USER"),
    },
    "3-days-left": {
        "promo": ts.get("ru", "NOTIFY_THREE_DAYS_LEFT_PROMO"),
        "regular": ts.get("ru", "NOTIFY_THREE_DAYS_LEFT"),
    },
    "1-day-left": {
        "promo": ts.get("ru", "NOTIFY_ONE_DAY_LEFT_PROMO"),
        "regular": ts.get("ru", "NOTIFY_ONE_DAY_LEFT"),
    },
    "nc-yesterday-created": {
        "random_list": NOT_CONNECTED_MESSAGES,
    },
    "purchase-success-non-autopay": {
        "static": ts.get("ru", "NOTIFY_SUCCESSFUL_NON_AUTOPAY"),
        "fallback": ts.get("ru", "NOTIFY_SUCCESSFUL_NON_AUTOPAY_FALLBACK"),
    },
    "referral_traffic_reached_bonus_applied": {
        "static": ts.get("ru", "NOTIFY_REFERRAL_TRAFFIC_REACHED_BONUS")
    },
    "referral_purchase_bonus_applied": {
        "static": ts.get("ru", "NOTIFY_REFERRAL_PURCHASE_BONUS_APPLIED")
    },
}

SILENT_NOTIFICATION_TYPES = {
    "purchase-failure-autopay",
    "purchase-failure-non-autopay",
}
