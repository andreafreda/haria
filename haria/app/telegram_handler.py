"""Handler Telegram: riceve messaggi/foto/PDF/vocali dagli utenti autorizzati,
li inoltra al motore conversazionale e rimanda la risposta. Espone il bot allo
scheduler per le notifiche proattive."""
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from claude_engine import chat, refresh_entity_cache
from memory import clear_history
import config as cfg

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4096


def _split_text(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Spezza un testo in chunk <= limit, preferendo i confini di riga."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        # riga singola troppo lunga: spezza a forza
        while len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        add = (("\n" + line) if buf else line)
        if len(buf) + len(add) > limit:
            chunks.append(buf)
            buf = line
        else:
            buf += add
    if buf:
        chunks.append(buf)
    return chunks or [""]


async def _reply(message, text: str, prefix: str = ""):
    """Invia una risposta in plain text, spezzandola se supera il limite Telegram.

    Plain text (niente parse_mode) evita crash 400 su markdown sbilanciato.
    `prefix` viene anteposto solo al primo chunk.
    """
    full = (prefix + (text or "")) if prefix else (text or "")
    if not full.strip():
        full = "(nessuna risposta)"
    for chunk in _split_text(full):
        await message.reply_text(chunk)


def _load_users() -> dict[str, dict]:
    return {str(u["chat_id"]): u for u in cfg.get("users", [])}


def _decode_barcode(img_bytes: bytes) -> str | None:
    """Decodifica un codice a barre (EAN/UPC) da un'immagine. None se assente."""
    try:
        import io
        from PIL import Image
        from pyzbar.pyzbar import decode
        img = Image.open(io.BytesIO(img_bytes))
        results = decode(img)
        if not results:
            return None
        # privilegia barcode prodotto
        for r in results:
            if r.type in ("EAN13", "EAN8", "UPCA", "UPCE"):
                return r.data.decode("ascii", "ignore")
        return results[0].data.decode("ascii", "ignore")
    except Exception as e:
        logger.debug("Decode barcode fallito: %s", e)
        return None


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


async def _handle_reload_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = str(update.effective_chat.id)
    if chat_id not in _load_users():
        return
    cfg.reload()
    await update.message.reply_text("Configurazione ricaricata da file.")


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
    await _reply(update.message, reply)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return

    chat_id = str(update.effective_chat.id)
    users = _load_users()

    if chat_id not in users:
        await update.message.reply_text("Non sei autorizzato a usare HARIA.")
        return

    if not cfg.get("modules", {}).get("voice", True):
        await update.message.reply_text("Trascrizione vocale disabilitata nella configurazione.")
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
    # plain text + prefisso trascrizione (niente Markdown: evita crash su caratteri speciali)
    await _reply(update.message, reply, prefix=f"« {text} »\n\n")


async def _handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return
    chat_id = str(update.effective_chat.id)
    users = _load_users()
    if chat_id not in users:
        await update.message.reply_text("Non sei autorizzato a usare HARIA.")
        return
    import base64
    photo = update.message.photo[-1]  # risoluzione massima
    photo_file = await photo.get_file()
    img_bytes = await photo_file.download_as_bytearray()
    img_b64 = base64.b64encode(bytes(img_bytes)).decode("ascii")
    caption = update.message.caption or ""

    await update.message.chat.send_action("typing")
    user_config = users[chat_id]

    # se c'è un codice a barre, risolvi il prodotto via lookup_barcode (dato esatto)
    code = _decode_barcode(bytes(img_bytes))
    if code:
        hint = (caption + " " if caption else "") + (
            f"[Codice a barre rilevato dalla foto: {code}. "
            "Usa lookup_barcode per i valori nutrizionali reali del prodotto, "
            "poi registra/aggiungi come richiesto (o chiedi quanto ne ha mangiato).]"
        )
        reply = await chat(chat_id, hint, user_config)
    else:
        reply = await chat(chat_id, caption, user_config, image_b64=img_b64, image_media_type="image/jpeg")
    await _reply(update.message, reply)


async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.document:
        return
    chat_id = str(update.effective_chat.id)
    users = _load_users()
    if chat_id not in users:
        await update.message.reply_text("Non sei autorizzato a usare HARIA.")
        return
    doc = update.message.document
    mime = (doc.mime_type or "").lower()
    fname = (doc.file_name or "").lower()
    caption = update.message.caption or ""
    is_pdf = "pdf" in mime or fname.endswith(".pdf")
    is_sheet = fname.endswith((".xlsx", ".csv")) or "spreadsheet" in mime or "excel" in mime \
        or mime in ("text/csv", "application/csv")

    # Estratto conto (xlsx/csv) -> import economia (se modulo abilitato)
    if is_sheet and not is_pdf:
        import modules as _mods
        if not _mods.owns("add_transazione"):
            await update.message.reply_text("Modulo economia non abilitato: non posso importare estratti.")
            return
        from modules import economia
        doc_file = await doc.get_file()
        content = bytes(await doc_file.download_as_bytearray())
        await update.message.chat.send_action("typing")
        try:
            reply = await economia.import_estratto_bytes(content, fname, caption, chat_id)
        except Exception as e:
            reply = f"Import fallito: {e}"
        await _reply(update.message, reply)
        return

    if not is_pdf:
        await update.message.reply_text("Gestisco PDF (diete/bollette) ed estratti conto xlsx/csv.")
        return
    import base64
    doc_file = await doc.get_file()
    pdf_bytes = await doc_file.download_as_bytearray()
    doc_b64 = base64.b64encode(bytes(pdf_bytes)).decode("ascii")

    await update.message.chat.send_action("typing")
    user_config = users[chat_id]
    reply = await chat(chat_id, caption, user_config, doc_b64=doc_b64)
    await _reply(update.message, reply)


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
    app.add_handler(CommandHandler("reloadconfig", _handle_reload_config))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, _handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, _handle_document))
    # refresh entity cache at startup and every 24h
    app.job_queue.run_once(_job_refresh_entities, when=10)
    app.job_queue.run_repeating(_job_refresh_entities, interval=86400, first=86400)
    return app
