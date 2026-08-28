import asyncio
import logging
import yaml
import os
from typing import Dict

import aiohttp

from utils.public_resources import (
    TELEGRAM_BOT_URL,
    TELEGRAM_CHANNEL_URL,
    TELEGRAM_SUPPORT_URL,
    PUBLIC_OFFER_URL,
)


PUBLIC_RESOURCE_PLACEHOLDERS = {
    "https://t.me/SHREDDER_VPN_BOT_PLACEHOLDER": TELEGRAM_BOT_URL,
    "https://t.me/SHREDDER_VPN_CHANNEL_PLACEHOLDER": TELEGRAM_CHANNEL_URL,
    "https://t.me/SHREDDER_VPN_SUPPORT_PLACEHOLDER": TELEGRAM_SUPPORT_URL,
    "https://SHREDDER_VPN_PUBLIC_OFFER_PLACEHOLDER": PUBLIC_OFFER_URL,
}


class Translator:
    def __init__(self, locales_dir: str = "locales"):
        self.locales_dir = locales_dir
        self.translations: Dict[str, Dict[str, str]] = {}
        self._load_translations()

        # Опциональная интеграция с vpn-bot-admin — включается configure_admin_sync().
        # Пока не вызвана: get() работает ровно как раньше, никуда не стучится.
        self._admin_base_url: str | None = None
        self._admin_api_key: str | None = None

    def _load_translations(self):
        """Загружаем все переводы из YAML файлов"""
        if not os.path.exists(self.locales_dir):
            print(f"Warning: locales directory '{self.locales_dir}' not found")
            return

        for lang_file in os.listdir(self.locales_dir):
            if lang_file.endswith(".yaml") or lang_file.endswith(".yml"):
                lang_code = lang_file.split(".")[0]
                try:
                    file_path = os.path.join(self.locales_dir, lang_file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        self.translations[lang_code] = yaml.safe_load(f)
                    print(f"Loaded translations for language: {lang_code}")
                except Exception as e:
                    print(f"Error loading {lang_file}: {e}")

    def configure_admin_sync(self, base_url: str, api_key: str) -> None:
        """Включает связь с vpn-bot-admin: заводит в админке недостающие шаблоны
        из yml, дальше периодически тянет оттуда актуальный текст, и на каждый
        известный ключ в get() шлёт событие 'сообщение отправлено' (не блокируя
        и не мешая боту, если админка недоступна)."""
        self._admin_base_url = base_url
        self._admin_api_key = api_key

    def get(self, lang: str, key: str, *format_args) -> str:
        """Получить переведенный текст"""

        # Получаем перевод или возвращаем ключ как фолбэк
        known = key in self.translations.get(lang, {})
        translation = self.translations.get(lang, {}).get(key, key)
        translation = self._replace_public_resource_placeholders(translation)

        if known:
            self._track_usage(key)

        # Форматируем строку если есть аргументы
        if format_args:
            try:
                return translation.format(*format_args)
            except (KeyError, IndexError) as e:
                print(f"Format error for key '{key}': {e}")
                return translation

        return translation

    def _track_usage(self, key: str) -> None:
        if not self._admin_base_url or not self._admin_api_key:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._send_usage_event(key))

    async def _send_usage_event(self, key: str) -> None:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)
            ) as session:
                await session.post(
                    f"{self._admin_base_url.rstrip('/')}/api/ingest/events",
                    headers={"X-API-Key": self._admin_api_key},
                    json={"event_type": key},
                )
        except Exception:
            logging.debug("translator: failed to send usage event for %s", key, exc_info=True)

    async def push_missing_to_admin(self, lang: str = "ru") -> None:
        """Заводит в vpn-bot-admin шаблоны, которых там ещё нет — источник это
        текущие locales/*.yml. Не перезаписывает то, что уже есть (правки через
        админку важнее). Сетевые ошибки только логируются, бот не падает."""
        if not self._admin_base_url or not self._admin_api_key:
            return
        texts = {
            key: value
            for key, value in self.translations.get(lang, {}).items()
            if isinstance(value, str) and value.strip()
        }
        if not texts:
            return
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(
                    f"{self._admin_base_url.rstrip('/')}/api/ingest/templates/sync",
                    headers={"X-API-Key": self._admin_api_key},
                    json={
                        "items": [
                            {"key": key, "title": key, "text": text}
                            for key, text in texts.items()
                        ]
                    },
                ) as response:
                    if response.status != 200:
                        logging.warning(
                            "translator: templates push failed, status=%s", response.status
                        )
                        return
                    data = await response.json()
                    logging.info(
                        "translator: templates push created=%s skipped=%s",
                        len(data.get("created", [])),
                        len(data.get("skipped", [])),
                    )
        except Exception:
            logging.exception("translator: failed to push templates to admin")

    async def refresh_from_admin(self, lang: str = "ru") -> None:
        """Подтягивает актуальные тексты шаблонов из vpn-bot-admin и накатывает
        их поверх текущих переводов в памяти — так правки в админке применяются
        без передеплоя бота. Если админка недоступна — оставляет то, что уже
        загружено (из yml или прошлого рефреша)."""
        if not self._admin_base_url or not self._admin_api_key:
            return
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(
                    f"{self._admin_base_url.rstrip('/')}/api/ingest/templates",
                    headers={"X-API-Key": self._admin_api_key},
                ) as response:
                    if response.status != 200:
                        logging.warning(
                            "translator: templates refresh failed, status=%s",
                            response.status,
                        )
                        return
                    data = await response.json()
        except Exception:
            logging.exception("translator: failed to refresh templates from admin")
            return

        lang_translations = self.translations.setdefault(lang, {})
        for item in data.get("items", []):
            key = item.get("key")
            text = item.get("text")
            if key and isinstance(text, str):
                lang_translations[key] = text

    def _replace_public_resource_placeholders(self, translation: str) -> str:
        if not isinstance(translation, str):
            return translation

        for placeholder, actual_url in PUBLIC_RESOURCE_PLACEHOLDERS.items():
            translation = translation.replace(placeholder, actual_url)

        return translation


# Глобальный инстанс
translator = Translator()


async def admin_templates_sync_loop(
    base_url: str | None, api_key: str | None, interval_seconds: int
) -> None:
    """Фоновая задача: один раз заводит в админке шаблоны из локальных yml,
    затем периодически подтягивает оттуда актуальные тексты. Ничего не делает,
    если admin_api_url/admin_api_key не заданы (интеграция опциональна)."""
    if not base_url or not api_key:
        logging.info("admin_templates_sync_loop: disabled (ADMIN_API_URL/ADMIN_API_KEY not set)")
        return

    translator.configure_admin_sync(base_url, api_key)
    await translator.push_missing_to_admin()

    while True:
        await asyncio.sleep(interval_seconds)
        await translator.refresh_from_admin()
