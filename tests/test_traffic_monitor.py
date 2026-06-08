from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import proto.rwmanager_pb2 as proto
from handlers.traffic_monitor import block_traffic_anomaly_user


class FakeSession:
    def __init__(self, user, snapshot) -> None:
        self.user = user
        self.snapshot = snapshot
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, model, user_id):
        return self.user if self.user.id == user_id else None

    async def scalar(self, statement):
        return self.snapshot

    async def commit(self):
        self.committed = True


class FakeRwmsClient:
    def __init__(self) -> None:
        self.request = None
        self.user = SimpleNamespace(
            uuid="rwms-uuid",
            active_internal_squads=[SimpleNamespace(uuid="squad-uuid")],
        )

    async def get_user_by_username(self, username):
        return self.user

    async def update_user(self, request):
        self.request = request
        return self.user


class TrafficMonitorHandlerTest(IsolatedAsyncioTestCase):
    async def test_blocks_user_and_marks_snapshot(self):
        user = SimpleNamespace(id=7, username="user-7")
        snapshot = SimpleNamespace(is_blocked=False)
        session = FakeSession(user, snapshot)
        rwms_client = FakeRwmsClient()
        query = SimpleNamespace(
            data="block_user:7",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_reply_markup=AsyncMock()),
            from_user=SimpleNamespace(id=123),
        )

        await block_traffic_anomaly_user(
            query=query,
            session_maker=lambda: session,
            rwms_client=rwms_client,
        )

        self.assertEqual(rwms_client.request.uuid, "rwms-uuid")
        self.assertEqual(rwms_client.request.status, proto.UserStatus.DISABLED)
        self.assertEqual(
            list(rwms_client.request.active_internal_squads),
            ["squad-uuid"],
        )
        self.assertTrue(snapshot.is_blocked)
        self.assertTrue(session.committed)
        query.message.edit_reply_markup.assert_awaited_once()
        query.answer.assert_awaited_once_with("Пользователь заблокирован")
