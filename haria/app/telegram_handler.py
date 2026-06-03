import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from claude_engine import chat, refresh_entity_cache
from memory import clear_history
import config as cfg

logger = logging.getLogger(__name__)


def _load_users() -> dict[str, dict]:
    return {str(u["chat_id"]): u for u in cfg.get("users", [])}


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    users = _load_users()
    if chat_id not in users:
        await update.message.reply_text(
            f"Non sei autorizzato. Il tuo chat_id è: {chat_id}"
        )
        return
    name = users[chat_id].get("name", "")
    await update.message.reply_text(
        f"Ciao {name}! Sono HARIA, il tuo assistente AI. Scrivimi o mandami un vocale."
    )


async def _handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    if chat_id not in _load_users():
        return
    await clear_history(chat_id)
    await update.message.reply_text("Memoria conversazione cancellata.")


async def _handle_update_entities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    if chat_id not in _load_users():
        return
    count = await refresh_entity_cache()
    await update.message.reply_text(f"Cache entità aggiornata: {count} entità caricate.")


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    chat_id = str(update.effective_chat.id)
    users = _load_users()

    if chat_id not in users:
        await update.message.reply_text("Non sei autorizzato a usare HARIA.")
        return

    user_config = users[chat_id]
    text = update.message.text or ""

    if not text.strip():
        return

    await update.message.chat.send_action("typing")
    reply = await chat(chat_id, text, user_config)
    await update.message.reply_text(reply)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return

    chat_id = str(update.effective_chat.id)
    users = _load_users()

    if chat_id not in users:
        return

    groq_key = cfg.get("groq_key", "")
    if not groq_key:
        await update.message.reply_text("Trascrizione vocale non configurata (groq_key mancante).")
        return

    voice_file = await update.message.voice.get_file()
    audio_bytes = await voice_file.download_as_bytearray()

    try:
        from groq import AsyncGroq
        import io
        groq_client = AsyncGroq(api_key=groq_key)
        transcription = await groq_client.audio.transcriptions.create(
            file=("audio.ogg", io.BytesIO(bytes(audio_bytes))),
            model="whisper-large-v3-turbo",
            language="it",
        )
        text = transcription.text
    except Exception as e:
        logger.error("Trascrizione fallita: %s", e)
        await update.message.reply_text("Errore durante la trascrizione audio.")
        return

    await update.message.chat.send_action("typing")
    user_config = users[chat_id]
    reply = await chat(chat_id, text, user_config)
    await update.message.reply_text(f"_{text}_\n\n{reply}", parse_mode="Markdown")


async def _job_refresh_entities(context):
    try:
        count = await refresh_entity_cache()
        logger.info("Refresh automatico cache entità: %d entità", count)
    except Exception as e:
        logger.error("Refresh automatico cache entità fallito: %s", e)


def build_app(token: str):
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(CommandHandler("reset", _handle_reset))
    app.add_handler(CommandHandler("updateentities", _handle_update_entities))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    # refresh entity cache at startup and every 24h
    app.job_queue.run_once(_job_refresh_entities, when=10)
    app.job_queue.run_repeating(_job_refresh_entities, interval=86400, first=86400)
    return app
