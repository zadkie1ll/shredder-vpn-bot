# Ключи локалей, а не сразу текст — резолвим через ts.get() в момент отправки
# (в utils/notifications.py), чтобы live-правки текста из vpn-bot-admin реально
# долетали до этих уведомлений.
NOTIFICATION_CONFIG = {
    "subscription-expired": {
        "promo_keys": [
            "NOTIFY_EXPIRED_USER_PROMO1",
            "NOTIFY_EXPIRED_USER_PROMO2",
            "NOTIFY_EXPIRED_USER_PROMO3",
            "NOTIFY_EXPIRED_USER_PROMO4",
            "NOTIFY_EXPIRED_USER_PROMO5",
        ],
        "regular_key": "NOTIFY_EXPIRED_USER",
    },
    "3-days-left": {
        "promo_keys": ["NOTIFY_THREE_DAYS_LEFT_PROMO"],
        "regular_key": "NOTIFY_THREE_DAYS_LEFT",
    },
    "1-day-left": {
        "promo_keys": ["NOTIFY_ONE_DAY_LEFT_PROMO"],
        "regular_key": "NOTIFY_ONE_DAY_LEFT",
    },
    "nc-yesterday-created": {
        "random_keys": [
            "NOTIFY_YESTERDAY_CREATED1",
            "NOTIFY_YESTERDAY_CREATED2",
            "NOTIFY_YESTERDAY_CREATED3",
        ],
    },
    "purchase-success-non-autopay": {
        "static_key": "NOTIFY_SUCCESSFUL_NON_AUTOPAY",
        "fallback_key": "NOTIFY_SUCCESSFUL_NON_AUTOPAY_FALLBACK",
    },
    "referral_traffic_reached_bonus_applied": {
        "static_key": "NOTIFY_REFERRAL_TRAFFIC_REACHED_BONUS"
    },
    "referral_purchase_bonus_applied": {
        "static_key": "NOTIFY_REFERRAL_PURCHASE_BONUS_APPLIED"
    },
}

SILENT_NOTIFICATION_TYPES = {
    "purchase-failure-autopay",
    "purchase-failure-non-autopay",
}
