import yaml
import os
from typing import Dict

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

    def get(self, lang: str, key: str, *format_args) -> str:
        """Получить переведенный текст"""

        # Получаем перевод или возвращаем ключ как фолбэк
        translation = self.translations.get(lang, {}).get(key, key)
        translation = self._replace_public_resource_placeholders(translation)

        # Форматируем строку если есть аргументы
        if format_args:
            try:
                return translation.format(*format_args)
            except (KeyError, IndexError) as e:
                print(f"Format error for key '{key}': {e}")
                return translation

        return translation

    def _replace_public_resource_placeholders(self, translation: str) -> str:
        if not isinstance(translation, str):
            return translation

        for placeholder, actual_url in PUBLIC_RESOURCE_PLACEHOLDERS.items():
            translation = translation.replace(placeholder, actual_url)

        return translation


# Глобальный инстанс
translator = Translator()
