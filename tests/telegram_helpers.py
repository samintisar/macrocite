"""A Bot API that answers from memory, for python-telegram-bot's Bot: no network, every call
recorded. Shared by the messenger and the bot tests."""

import json
from typing import Any

from telegram.request import BaseRequest, RequestData

TOKEN = "123456:TEST-TOKEN"  # the shape of a real token; never sent anywhere
CHAT = 4242  # the owner's chat in these tests


class FakeTelegram(BaseRequest):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id = 500

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def read_timeout(self) -> float | None:
        return 1.0

    async def do_request(
        self, url: str, method: str, request_data: RequestData | None = None, **_: Any
    ) -> tuple[int, bytes]:
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data is not None else {}
        self.calls.append((endpoint, params))
        return 200, json.dumps({"ok": True, "result": self._result(endpoint, params)}).encode()

    def _result(self, endpoint: str, params: dict[str, Any]) -> object:
        if endpoint == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "SignalBench",
                    "username": "signalbench_test_bot"}
        if endpoint == "sendMessage":
            self._next_id += 1
            return {"message_id": self._next_id, "date": 0, "text": params["text"],
                    "chat": {"id": params["chat_id"], "type": "private"}}
        if endpoint == "editMessageText":
            return {"message_id": params["message_id"], "date": 0, "text": params["text"],
                    "chat": {"id": params["chat_id"], "type": "private"}}
        return True  # answerCallbackQuery

    def sent(self, endpoint: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == endpoint]


class Rejecting(FakeTelegram):
    """A Bot API that rejects the token, as Telegram answers a revoked or mistyped one."""

    async def do_request(
        self, url: str, method: str, request_data: RequestData | None = None, **_: Any
    ) -> tuple[int, bytes]:
        self.calls.append((url.rsplit("/", 1)[-1], {}))
        return 401, json.dumps(
            {"ok": False, "error_code": 401, "description": "Unauthorized"}
        ).encode()
