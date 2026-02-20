import asyncio
import logging
import alembic.config

from alembic import command
from aiogram import Bot
from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from utils.config import Config
from handlers import handlers_router
from handlers.technical_work import technical_work_router
from utils.notifications import listen_notifications
from utils.redis_message_broker import RedisMessageBroker
from middlewares.throttle import ThrottleMiddleware
from middlewares.global_error import GlobalErrorMiddleware
from middlewares.display_name_restriction import DisplayNameRestrictionMiddleware
from common.rwms_client import RwmsClient
from common.setup_logger import setup_logger


async def main(config: Config) -> None:
    try:
        pg_config = f"{config.pg_user}:{config.pg_password}@{config.pg_host}:{config.pg_port}/{config.pg_db}"
        alembic_cfg = alembic.config.Config()
        alembic_cfg.set_main_option("script_location", "common/alembic")
        alembic_cfg.set_main_option("sqlalchemy.url", f"postgresql://{pg_config}")
        alembic_cfg.print_stdout = logging.info
        command.upgrade(alembic_cfg, "head")

        dispatcher = Dispatcher()

        # 1. Сначала защита от DDoS и спама
        dispatcher.update.outer_middleware(ThrottleMiddleware(limit=0.8, ban_time=60))

        # 2. Затем ограничение по именам
        dispatcher.message.middleware(DisplayNameRestrictionMiddleware())
        dispatcher.callback_query.middleware(DisplayNameRestrictionMiddleware())

        # 3. Глобальный обработчик остальных ошибок (BadRequest и т.д.)
        dispatcher.message.middleware(GlobalErrorMiddleware())
        dispatcher.callback_query.middleware(GlobalErrorMiddleware())

        bot = Bot(
            token=config.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logging.info("Webhook deleted successfully")
        except Exception as e:
            logging.warning(f"Failed to delete webhook: {e}")

        redis_message_broker = RedisMessageBroker(config=config)
        rwms_client = RwmsClient(addr=config.rwms_address, port=config.rwms_port)

        sql_engine = create_async_engine(
            f"postgresql+asyncpg://{pg_config}",
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        session_maker = async_sessionmaker(bind=sql_engine, expire_on_commit=False)

        if config.technical_work_enabled:
            dispatcher.include_router(technical_work_router)
        else:
            dispatcher.include_router(handlers_router)

        asyncio.create_task(
            listen_notifications(
                bot=bot,
                redis_message_broker=redis_message_broker,
                session_maker=session_maker,
            )
        )

        await dispatcher.start_polling(
            bot,
            config=config,
            rwms_client=rwms_client,
            redis_message_broker=redis_message_broker,
            session_maker=session_maker,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logging.info("received shutdown signal")


if __name__ == "__main__":
    config = Config()

    log_level = logging.INFO

    if config.log_level.lower() == "debug":
        log_level = logging.DEBUG
    if config.log_level.lower() == "info":
        log_level = logging.INFO
    if config.log_level.lower() == "warning":
        log_level = logging.WARN
    if config.log_level.lower() == "error":
        log_level = logging.ERROR
    if config.log_level.lower() == "critical":
        log_level = logging.CRITICAL

    setup_logger(filename="monkey-island-vpn-bot.log", level=log_level)
    logging.getLogger("aiogram").setLevel(log_level)

    try:
        asyncio.run(main(config=config))
    except KeyboardInterrupt:
        logging.info("program interrupted by user")
