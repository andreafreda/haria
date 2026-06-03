"""Holder del bot Telegram per invii proattivi (messaggi a membri, reminder)."""
import logging

logger = logging.getLogger(__name__)

_bot = None


def set_bot(bot):
    global _bot
    _bot = bot


def get_bot():
    return _bot


async def send(chat_id, text: str):
    if _bot is None:
        raise RuntimeError("Bot Telegram non inizializzato")
    await _bot.send_message(chat_id=int(chat_id), text=text)
