import logging
import asyncio
from wrapt import partial
from yookassa import Payment
from yookassa import Configuration

from common.models.db import User
from common.models.tariff import Tariff
from common.models.tariff import TrialPromotionTariff
from utils.public_resources import TELEGRAM_BOT_URL


def create_payment_sync(
    shop_id: str, secret: str, tariff: Tariff, username: str, telegram_id: int
) -> str:
    Configuration.account_id = shop_id
    Configuration.secret_key = secret

    payment = Payment.create(
        {
            "save_payment_method": True,
            "amount": {"value": tariff.price, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": TELEGRAM_BOT_URL,
            },
            "metadata": {
                "username": username,
                "telegram_id": telegram_id,
                "subscription_period": tariff.db_tariff_id,
                "autopay": False,
                "trial_promotion": isinstance(tariff, TrialPromotionTariff),
                "from_trial": False,
            },
            "capture": True,
            "description": tariff.description,
        }
    )

    return payment.confirmation.confirmation_url


async def create_payment(
    shop_id: str, secret: str, tariff: Tariff, database_user: User
) -> str:
    """Асинхронная версия создания платежа"""

    loop = asyncio.get_event_loop()

    try:
        sync_func = partial(
            create_payment_sync,
            shop_id=shop_id,
            secret=secret,
            tariff=tariff,
            username=database_user.username,
            telegram_id=database_user.telegram_id,
        )

        payment_url = await asyncio.wait_for(
            loop.run_in_executor(None, sync_func), timeout=15.0
        )

        return payment_url

    except asyncio.TimeoutError:
        logging.error(
            f"timeout creating YooKassa payment for user {database_user.username}"
        )
        raise
    except Exception as e:
        logging.error(
            f"error creating YooKassa payment for user {database_user.username}: {e}"
        )
        raise
