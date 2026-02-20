from aiogram.fsm.state import State, StatesGroup


class BroadcastStates(StatesGroup):
    confirm = State()
