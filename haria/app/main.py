import asyncio
import logging
import os
import signal
import aiohttp
import config as cfg
from memory import init_db
from telegram_handler import build_app
import scheduler

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("haria")

_previous_webhook: dict | None = None


async def _get_webhook_info(token: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
    return data.get("result", {})


async def _delete_webhook(token: str):
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"drop_pending_updates": False}) as resp:
            return await resp.json()


async def _set_webhook(token: str, webhook_url: str, secret: str | None = None):
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    payload: dict = {"url": webhook_url}
    if secret:
        payload["secret_token"] = secret
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()


async def _acquire_bot(token: str) -> bool:
    """
    Save existing webhook info, then delete it so HARIA can use polling.
    Returns True if takeover was clean.
    """
    global _previous_webhook
    try:
        info = await _get_webhook_info(token)
        _previous_webhook = info
        existing_url = info.get("url", "")
        if existing_url:
            logger.info("Webhook esistente trovato: %s — sospeso per HARIA", existing_url)
        await _delete_webhook(token)
        logger.info("Bot Telegram acquisito in modalità polling.")
        return True
    except Exception as e:
        logger.error("Impossibile acquisire il bot Telegram: %s", e)
        return False


async def _release_bot(token: str):
    """
    Restore previous webhook if one existed, so HA integration resumes cleanly.
    """
    global _previous_webhook
    if not _previous_webhook:
        return
    prev_url = _previous_webhook.get("url", "")
    if not prev_url:
        logger.info("Nessun webhook precedente — bot lasciato libero.")
        return
    try:
        secret = _previous_webhook.get("secret_token")
        await _set_webhook(token, prev_url, secret)
        logger.info("Webhook HA ripristinato: %s", prev_url)
    except Exception as e:
        logger.warning("Impossibile ripristinare webhook HA: %s", e)


async def main():
    token = cfg.get("telegram_token") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        logger.error("telegram_token non configurato")
        return

    logger.info("Inizializzazione DB...")
    await init_db()

    acquired = await _acquire_bot(token)
    if not acquired:
        logger.error("Impossibile avviare: bot Telegram non acquisibile")
        return

    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    if cfg.get("modules", {}).get("reminders", False):
        await scheduler.start(app.bot)

    logger.info("HARIA attiva. In attesa di messaggi.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal(signum, frame):
        logger.info("Segnale di stop ricevuto (%s).", signum)
        loop.call_soon_threadsafe(stop_event.set)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    await stop_event.wait()

    logger.info("Arresto HARIA...")
    scheduler.shutdown()
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    await _release_bot(token)
    logger.info("HARIA fermata. Bot Telegram rilasciato.")


if __name__ == "__main__":
    asyncio.run(main())
