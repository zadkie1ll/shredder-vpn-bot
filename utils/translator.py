import yaml
import os
from typing import Dict


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

        # Форматируем строку если есть аргументы
        if format_args:
            try:
                return translation.format(*format_args)
            except (KeyError, IndexError) as e:
                print(f"Format error for key '{key}': {e}")
                return translation

        return translation


# Глобальный инстанс
translator = Translator()
