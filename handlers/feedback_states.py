from aiogram.fsm.state import State, StatesGroup


class FeedbackBroadcastStates(StatesGroup):
    confirm = State()
