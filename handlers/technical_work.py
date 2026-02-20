from aiogram import Router
from utils.translator import translator as ts

technical_work_router = Router()


@technical_work_router.message()
async def technical_work_message_handler(message):
    await message.answer(text=ts.get("ru", "TECHNICAL_WORK_MESSAGE"))
