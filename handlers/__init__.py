from aiogram import Router
from .menu import menu_router
from .install import install_router
from .questions import questions_router
from .tariffs import tariffs_router
from .cancel_subscription import cancel_subscription_router
from .service import service_router

handlers_router = Router()

# Подключаем дочерние роутеры
handlers_router.include_routers(
    menu_router,
    install_router,
    questions_router,
    tariffs_router,
    cancel_subscription_router,
    service_router,
)
