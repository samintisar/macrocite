"""Spec 05, Telegram and processes: the python-telegram-bot glue and the heartbeat, against a
Bot API that answers from memory (no network)."""

import asyncio
import io
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session
from telegram import Bot, Update
from telegram.error import InvalidToken
from telegram.ext import TypeHandler

from paper_helpers import evening
from scan_helpers import DAYS, OWNER, B, World, bot_brain, make_world
from signalbench.live.heartbeat import heartbeat_problem, write_heartbeat
from signalbench.live.telegram_bot import (
    RedactingFormatter,
    beat_once,
    build_application,
    handle_update,
)
from telegram_helpers import TOKEN, FakeTelegram

USER = {"id": OWNER, "is_bot": False, "first_name": "Owner"}


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    world.scan(B)
    return world


def _run(api: FakeTelegram, updates: list[dict[str, Any]], world: World) -> None:
    async def main() -> None:
        bot = Bot(TOKEN, request=api, get_updates_request=api)
        await bot.initialize()
        brain = bot_brain(world)
        for data in updates:
            await handle_update(Update.de_json(data, bot), brain, bot)
        await bot.shutdown()

    asyncio.run(main())


def _message(chat: int, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {
        "message_id": 9, "date": 0, "chat": {"id": chat, "type": "private"},
        "from": {**USER, "id": chat}, "text": text,
    }}


def test_a_command_is_answered_in_the_owners_chat(world: World) -> None:
    api = FakeTelegram()
    _run(api, [_message(OWNER, "/deposit 50")], world)
    [sent] = api.sent("sendMessage")
    assert (sent["chat_id"], sent["text"]) == (
        OWNER, "Deposit of C$50.00 recorded on 2026-10-06. Cash is now C$150.00."
    )


def test_a_button_press_is_answered_and_its_reply_asks_for_the_fill(world: World) -> None:
    entry = world.messenger.sent[0]
    press = {"update_id": 2, "callback_query": {
        "id": "cb-1", "from": USER, "chat_instance": "ci", "data": "b:1",
        "message": {"message_id": entry.message_id, "date": 0, "text": entry.text,
                    "chat": {"id": OWNER, "type": "private"}},
    }}
    api = FakeTelegram()
    _run(api, [press], world)
    assert [name for name, _ in api.calls] == ["getMe", "answerCallbackQuery", "sendMessage"]
    [ask] = api.sent("sendMessage")
    assert ask["text"].startswith("What was your average fill price for the 3 units of ZNVD?")
    assert "reply_markup" not in ask  # nothing is assumed: the owner types the fill


def test_a_foreign_chat_gets_no_answer_at_all(world: World) -> None:
    api = FakeTelegram()
    press = {"update_id": 3, "callback_query": {
        "id": "cb-2", "from": {**USER, "id": 999}, "chat_instance": "ci", "data": "b:1",
        "message": {"message_id": 5, "date": 0, "text": "x", "chat": {"id": 999, "type": "private"}},
    }}
    _run(api, [_message(999, "/portfolio"), press], world)
    assert [name for name, _ in api.calls] == ["getMe"]  # nothing sent, not even an answer


def test_the_application_takes_every_update_to_one_handler_without_the_network(
    world: World,
) -> None:
    api = FakeTelegram()
    app = build_application(TOKEN, bot_brain(world), lambda: None, request=api)
    [[handler]] = app.handlers.values()
    assert isinstance(handler, TypeHandler)
    assert api.calls == []


def test_a_heartbeat_needs_telegram_to_answer_and_the_scan_sees_its_age(world: World) -> None:
    session = world.session
    now = evening(DAYS[B + 1])

    def write() -> None:
        write_heartbeat(Session(session.get_bind()), now)

    async def beat(api: FakeTelegram) -> bool:
        bot = Bot(TOKEN, request=api, get_updates_request=api)
        await bot.initialize()
        return await beat_once(bot, write)

    assert asyncio.run(beat(FakeTelegram())) is True
    assert heartbeat_problem(session, now + timedelta(minutes=59)) is None
    assert heartbeat_problem(session, now + timedelta(hours=2)) == (
        "the bot last checked in 2.0 hours ago (2026-10-06 18:00 New York time): is it running?"
    )

    class Down(FakeTelegram):
        def _result(self, endpoint: str, params: dict[str, Any]) -> object:
            if endpoint == "getMe" and len(self.calls) > 1:
                raise ConnectionError("no network")
            return super()._result(endpoint, params)

    assert asyncio.run(beat(Down())) is False  # the first getMe initializes; the beat fails


def test_an_update_delivered_again_after_a_crash_is_recorded_once(world: World) -> None:
    api = FakeTelegram()
    deposit = _message(OWNER, "/deposit 50")
    _run(api, [deposit, deposit], world)
    first, again = (sent["text"] for sent in api.sent("sendMessage"))
    assert first == "Deposit of C$50.00 recorded on 2026-10-06. Cash is now C$150.00."
    assert again == "⚠️ Already recorded: Telegram delivered update 1 again."
    assert world.ledger(B + 1).cash() == Decimal("150.00")


def test_log_lines_and_tracebacks_never_show_the_token() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(TOKEN, "%(levelname)s %(message)s"))
    logger = logging.getLogger("signalbench.test.redaction")
    logger.addHandler(handler)
    try:
        try:
            raise InvalidToken(f"The token `{TOKEN}` was rejected by the server.")
        except InvalidToken:
            logger.exception("polling failed for %s", f"https://api.telegram.org/bot{TOKEN}/getMe")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert TOKEN not in text
    assert text.startswith("ERROR polling failed for https://api.telegram.org/bot***/getMe\n")
    assert "InvalidToken: The token `***` was rejected by the server." in text
