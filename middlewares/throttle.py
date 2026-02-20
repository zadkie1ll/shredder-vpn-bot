import time
import logging
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.exceptions import TelegramForbiddenError

logger = logging.getLogger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 0.3, ban_time: int = 300):
        self.limit = limit
        self.ban_time = ban_time
        self.last_request = {}  # user_id: timestamp
        self.blacklist = {}  # user_id: ban_expiry_timestamp
        self.spam_count = {}  # user_id: количество быстрых нажатий подряд

    async def __call__(self, handler, event: TelegramObject, data):
        # В outer_middleware на уровне Update, данные о пользователе лежат в data
        user = data.get("event_from_user")
        chat = data.get("event_chat")

        if chat and chat.id < 0:
            # Игнорируем групповые чаты
            logging.warning(f"⚠️ ThrottleMiddleware skipped for group chat {chat.id}")
            return None

        # Если это техническое обновление без пользователя (например, poll или my_chat_member)
        if not user or user.is_bot:
            return await handler(event, data)

        user_id = user.id
        now = time.time()

        # 1. Проверка жесткого бана (для тех, кто удалил чат или злостных спамеров)
        if user_id in self.blacklist:
            if now < self.blacklist[user_id]:
                return  # Полный игнор
            else:
                del self.blacklist[user_id]
                self.spam_count[user_id] = 0

        # 2. Проверка мягкого лимита (троттлинг)
        last_time = self.last_request.get(user_id, 0)
        if now - last_time < self.limit:
            # Считаем количество быстрых нажатий
            self.spam_count[user_id] = self.spam_count.get(user_id, 0) + 1

            # Если нажал быстро 5 раз подряд — вешаем бан на 1 минуту и предупреждаем
            if self.spam_count[user_id] >= 10:
                self.blacklist[user_id] = now + 60
                logger.warning(f"🚨 USER {user_id} BANNED FOR PERSISTENT SPAM")

                # Здесь событие (event) — это Update, поэтому проверяем вложения
                try:
                    if update_msg := event.message:
                        await update_msg.answer(
                            "⏳ Вы слишком часто нажимаете! Отдохните минуту."
                        )
                    elif update_cb := event.callback_query:
                        await update_cb.answer(
                            "⏳ Слишком часто нажимаете! Отдохните минуту.",
                            show_alert=True,
                        )
                except Exception:
                    pass

            return  # Просто игнорируем запрос, не выполняя хендлер

        # Если интервал между нажатиями в норме — сбрасываем счетчик спама
        self.spam_count[user_id] = 0
        self.last_request[user_id] = now

        if len(self.last_request) > 5000:
            now = time.time()
            self.last_request = {
                u: t for u, t in self.last_request.items() if now - t < 60
            }
            self.blacklist = {u: t for u, t in self.blacklist.items() if t > now}
            self.spam_count = {
                u: c for u, c in self.spam_count.items() if u in self.last_request
            }

        try:
            return await handler(event, data)
        except TelegramForbiddenError:
            # А вот тут баним жестко и надолго (DDoS атака)
            self.blacklist[user_id] = now + self.ban_time
            logger.warning(
                f"🛡️ DDoS PROTECTION: User {user_id} removed chat. Banned for {self.ban_time}s"
            )
            return
