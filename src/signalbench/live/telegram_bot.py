"""The bot process (spec 05, Telegram and processes): python-telegram-bot with long polling.

One handler takes every update to `BotBrain` (live/bot.py) and carries its replies back:
new messages, or edits of the message whose button was pressed. Only the owner's chat is
answered. A background task checks that Telegram answers and then writes the heartbeat every
five minutes, so the evening scan notices a bot that is down without exiting.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from telegram import Bot, Message, Update
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationBuilder, ContextTypes, TypeHandler
from telegram.request import BaseRequest

from signalbench.live.bot import BotBrain, Reply
from signalbench.live.messenger import chunks, keyboard

HEARTBEAT_SECONDS = 300.0  # at least every 10 minutes while polling (spec 05)
log = logging.getLogger(__name__)


async def send_replies(bot: Bot, chat_id: int, replies: list[Reply]) -> None:
    for reply in replies:
        if reply.edit is None:
            pieces = chunks(reply.text)
            for index, piece in enumerate(pieces):
                last = index == len(pieces) - 1
                markup = keyboard(reply.buttons) if last else None
                await bot.send_message(chat_id, piece, reply_markup=markup)
            continue
        try:
            await bot.edit_message_text(
                chunks(reply.text)[0], chat_id=chat_id, message_id=reply.edit,
                reply_markup=keyboard(reply.buttons),
            )
        except BadRequest as error:  # e.g. "message is not modified": the reply still stands
            log.warning("could not edit message %s: %s", reply.edit, error)


async def handle_update(update: Update, brain: BotBrain, bot: Bot) -> None:
    """A command, a reply, or a button press from the owner's chat. Anything else is logged
    by the brain and gets no answer at all."""
    chat = update.effective_chat
    if chat is None or not brain.allowed(chat.id):
        return
    query = update.callback_query
    if query is not None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        replies = brain.handle_callback(
            chat.id, query.data or "", 0 if message is None else message.message_id,
            "" if message is None or message.text is None else message.text,
        )
    elif update.message is not None and update.message.text:
        replies = brain.handle_text(chat.id, update.message.text)
    else:
        return
    await send_replies(bot, chat.id, replies)


async def beat_once(bot: Bot, write: Callable[[], None]) -> bool:
    """Telegram answers (getMe), then the heartbeat row is written. False when either failed."""
    try:
        await bot.get_me()
        write()
    except Exception:  # logged; the next beat tries again
        log.exception("heartbeat failed")
        return False
    return True


async def beat_forever(bot: Bot, write: Callable[[], None], every: float) -> None:
    while True:
        await beat_once(bot, write)
        await asyncio.sleep(every)


def build_application(
    token: str,
    brain: BotBrain,
    write_heartbeat: Callable[[], None],
    *,
    request: BaseRequest | None = None,
    every: float = HEARTBEAT_SECONDS,
) -> Application[Any, Any, Any, Any, Any, Any]:
    """The Application `bot run` polls with. `request` replaces the network (tests)."""
    tasks: list[asyncio.Task[None]] = []

    async def start(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        tasks.append(asyncio.create_task(beat_forever(app.bot, write_heartbeat, every)))

    async def stop(_app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        for task in tasks:
            task.cancel()

    builder = ApplicationBuilder().token(token).post_init(start).post_shutdown(stop)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()

    async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await handle_update(update, brain, context.bot)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("update %s failed", update, exc_info=context.error)

    app.add_handler(TypeHandler(Update, on_update))
    app.add_error_handler(on_error)
    return app
