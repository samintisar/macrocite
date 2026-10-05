"""Spec 05, Telegram and processes: the Messenger protocol, its fake and console versions, and
TelegramMessenger against a Bot API that answers from memory (no network)."""

from typing import Any

import pytest
from telegram.error import InvalidToken, NetworkError

from signalbench.live.messenger import (
    MAX_TEXT,
    Button,
    ConsoleMessenger,
    FakeMessenger,
    Messenger,
    TelegramMessenger,
    chunks,
    redact,
)
from telegram_helpers import CHAT, TOKEN, FakeTelegram, Rejecting

ROW = ((Button("✅ I bought", "b:12"), Button("⏭ Skip", "s:12")),)


def test_button_data_must_fit_telegrams_64_bytes() -> None:
    Button("ok", "x" * 64)
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("too long", "x" * 65)
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("multi-byte", "é" * 33)  # 66 bytes in UTF-8
    with pytest.raises(ValueError, match="1-64 bytes"):
        Button("empty", "")


def test_long_text_is_cut_on_line_breaks_into_telegram_sized_pieces() -> None:
    line = "x" * 1000
    text = "\n".join([line] * 9)  # 9008 characters
    pieces = chunks(text)
    assert [len(piece) for piece in pieces] == [4003, 4003, 1000]
    assert "\n".join(pieces) == text
    assert chunks("y" * (MAX_TEXT + 5)) == ["y" * MAX_TEXT, "y" * 5]


def test_the_fake_records_sends_and_edits_and_can_fail() -> None:
    fake = FakeMessenger()
    messenger: Messenger = fake
    first = messenger.send("hello", ROW)
    messenger.edit(first, "hello again")
    assert [(m.message_id, m.text, m.buttons) for m in fake.sent] == [(first, "hello", ROW)]
    assert fake.edits == [(first, "hello again", ())]
    fake.fail = True
    with pytest.raises(ConnectionError):
        messenger.send("lost")
    assert fake.texts() == ["hello"]


def test_the_console_messenger_prints_each_message_and_its_buttons() -> None:
    lines: list[str] = []
    console = ConsoleMessenger(lines.append)
    assert console.send("line one\nline two", ROW) == 1
    console.edit(1, "changed")
    assert lines == [
        "--- message 1 ---", "line one\nline two", "[✅ I bought] [⏭ Skip]",
        "--- edit of message 1 ---", "changed",
    ]


def test_telegram_messenger_sends_and_edits_through_the_bot_api() -> None:
    api = FakeTelegram()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    assert api.calls == []  # it connects on the first send, so a scan runs without Telegram
    message_id = messenger.send("🟢 BUY", ROW)
    messenger.edit(message_id, "🟢 BUY\n✅ taken")
    messenger.close()
    assert [name for name, _ in api.calls] == ["getMe", "sendMessage", "editMessageText"]
    [sent] = api.sent("sendMessage")
    assert sent == {
        "chat_id": CHAT, "text": "🟢 BUY",
        "reply_markup": {"inline_keyboard": [[
            {"text": "✅ I bought", "callback_data": "b:12"},
            {"text": "⏭ Skip", "callback_data": "s:12"},
        ]]},
    }
    [edited] = api.sent("editMessageText")
    assert (edited["message_id"], edited["text"], "reply_markup" in edited) == (
        message_id, "🟢 BUY\n✅ taken", False
    )


def test_telegram_messenger_splits_a_long_message_and_buttons_go_on_the_last_piece() -> None:
    api = FakeTelegram()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    last = messenger.send("\n".join(["z" * 3000] * 2), ROW)
    messenger.close()
    first, second = api.sent("sendMessage")
    assert ("reply_markup" in first, "reply_markup" in second) == (False, True)
    assert last == 502  # the second message's id: its buttons are the ones pressed later


def test_a_failed_connection_is_tried_again_by_the_next_send() -> None:
    class Offline(FakeTelegram):
        def _result(self, endpoint: str, params: dict[str, Any]) -> object:
            if endpoint == "getMe" and len(self.sent("getMe")) == 1:
                raise ConnectionError("no network")
            return super()._result(endpoint, params)

    api = Offline()
    messenger = TelegramMessenger(TOKEN, CHAT, request=api)
    with pytest.raises(NetworkError, match="no network"):
        messenger.send("lost")
    assert messenger.send("sent") == 501
    messenger.close()
    assert [name for name, _ in api.calls] == ["getMe", "getMe", "sendMessage"]


def test_a_rejected_token_never_appears_in_the_error() -> None:
    messenger = TelegramMessenger(TOKEN, CHAT, request=Rejecting())
    with pytest.raises(InvalidToken) as caught:
        messenger.send("lost")
    assert str(caught.value) == "The token `***` was rejected by the server."
    assert TOKEN not in repr(caught.value)
    assert redact(f"bot{TOKEN}/getMe", TOKEN) == "bot***/getMe"
    assert redact("nothing to hide", "") == "nothing to hide"
