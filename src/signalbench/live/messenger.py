"""How the evening scan talks to Telegram (spec 05, Telegram and processes).

A `Messenger` sends a message with inline buttons and returns its id, and edits a message
later. `TelegramMessenger` sends through the Bot API with python-telegram-bot; `FakeMessenger`
records messages for tests; `ConsoleMessenger` prints them (`scan --dry-run`). Button presses
and commands are handled only by the bot process (live/telegram_bot.py).
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol, TypeVar

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import BaseRequest

MAX_TEXT = 4096  # Bot API: a message is 1-4096 characters
MAX_CALLBACK_BYTES = 64  # Bot API: callback_data is 1-64 bytes
T = TypeVar("T")


@dataclass(frozen=True)
class Button:
    """An inline button. `data` comes back to the bot when it is pressed."""

    text: str
    data: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.data.encode()) <= MAX_CALLBACK_BYTES:
            raise ValueError(f"button data must be 1-64 bytes in UTF-8, not {self.data!r}")


Buttons = tuple[tuple[Button, ...], ...]  # rows of buttons


class Messenger(Protocol):
    def send(self, text: str, buttons: Buttons = ()) -> int:
        """Send a message; returns its Telegram message id."""
        ...

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        """Replace a message's text and buttons (no buttons removes them)."""
        ...


def chunks(text: str, limit: int = MAX_TEXT) -> list[str]:
    """`text` cut on line breaks into pieces of at most `limit` characters (a longer line is
    cut where it must be)."""
    pieces: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:limit])
            line = line[limit:]
        joined = f"{current}\n{line}" if current else line
        if current and len(joined) > limit:
            pieces.append(current)
            current = line
        else:
            current = joined
    return [*pieces, current] if current or not pieces else pieces


def keyboard(buttons: Buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.text, callback_data=b.data) for b in row] for row in buttons]
    )


@dataclass(frozen=True)
class Sent:
    message_id: int
    text: str
    buttons: Buttons


@dataclass
class FakeMessenger:
    """Records messages; `fail` makes every send raise, as if Telegram were unreachable."""

    fail: bool = False
    sent: list[Sent] = field(default_factory=list)
    edits: list[tuple[int, str, Buttons]] = field(default_factory=list)
    next_id: int = 100

    def send(self, text: str, buttons: Buttons = ()) -> int:
        if self.fail:
            raise ConnectionError("Telegram is unreachable (FakeMessenger)")
        self.next_id += 1
        self.sent.append(Sent(self.next_id, text, buttons))
        return self.next_id

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        if self.fail:
            raise ConnectionError("Telegram is unreachable (FakeMessenger)")
        self.edits.append((message_id, text, buttons))

    def texts(self) -> list[str]:
        return [message.text for message in self.sent]


class ConsoleMessenger:
    """Prints every message and its buttons (`scan --dry-run`); nothing is sent."""

    def __init__(self, echo: Callable[[str], None]) -> None:
        self._echo = echo
        self._count = 0

    def send(self, text: str, buttons: Buttons = ()) -> int:
        self._count += 1
        self._echo(f"--- message {self._count} ---")
        self._echo(text)
        for row in buttons:
            self._echo(" ".join(f"[{b.text}]" for b in row))
        return self._count

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        self._echo(f"--- edit of message {message_id} ---")
        self._echo(text)
        for row in buttons:
            self._echo(" ".join(f"[{b.text}]" for b in row))


class TelegramMessenger:
    """The Bot API through python-telegram-bot's Bot, run on a private event loop so the
    synchronous scan can call it. It connects on the first send, so a scan still runs, and
    records its rows, when Telegram is down (the next scan sends what was not sent); a failed
    connection is tried again by the next call."""

    def __init__(self, token: str, chat_id: int, *, request: BaseRequest | None = None) -> None:
        self._bot = Bot(token, request=request)
        self._chat_id = chat_id
        self._loop = asyncio.new_event_loop()
        self._ready = False

    def _run(self, make: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if not self._ready:
            self._loop.run_until_complete(self._bot.initialize())  # getMe checks the token
            self._ready = True
        return self._loop.run_until_complete(make())

    def send(self, text: str, buttons: Buttons = ()) -> int:
        pieces = chunks(text)
        message_id = 0
        for index, piece in enumerate(pieces):
            markup = keyboard(buttons) if index == len(pieces) - 1 else None
            message = self._run(
                partial(self._bot.send_message, self._chat_id, piece, reply_markup=markup)
            )
            message_id = message.message_id
        return message_id

    def edit(self, message_id: int, text: str, buttons: Buttons = ()) -> None:
        self._run(
            partial(
                self._bot.edit_message_text, chunks(text)[0], chat_id=self._chat_id,
                message_id=message_id, reply_markup=keyboard(buttons),
            )
        )

    def close(self) -> None:
        if self._ready:
            self._loop.run_until_complete(self._bot.shutdown())
            self._ready = False
        self._loop.close()
