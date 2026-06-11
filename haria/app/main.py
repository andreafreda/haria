"""Entrypoint HARIA: avvia i sottosistemi abilitati (Telegram, scheduler,
job proattivi food, briefing news, pannello web ingress) e tiene vivo il loop."""
import asyncio
import logging
import os
import time
import signal

# Timezone: il container parte in UTC ma utenti/cron sono in orario locale.
# Fissa TZ prima di creare scheduler (tzlocal) e usare datetime.now() (engine),
# così cron e promemoria scattano all'ora locale attesa. Override via env TZ.
os.environ.setdefault("TZ", "Europe/Rome")
try:
    time.tzset()
except AttributeError:
    pass
import aiohttp
import config as cfg
from memory import init_db
from telegram_handler import build_app
import scheduler
import notifier
import webpanel
import mqtt_pub
import errorlog

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
        # NB: getWebhookInfo di Telegram non espone secret_token, quindi il
        # webhook viene ripristinato SENZA secret. Se l'integrazione HA ne usava
        # uno, va riconfigurato a mano (non recuperabile via API).
        await _set_webhook(token, prev_url, None)
        logger.warning("Webhook HA ripristinato SENZA secret_token (Telegram non "
                       "lo espone): se l'integrazione HA usava un secret, "
                       "riconfigurala.")
    except Exception as e:
        logger.warning("Impossibile ripristinare webhook HA: %s", e)


async def main():
    token = cfg.get("telegram_token") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        logger.error("telegram_token non configurato")
        return

    logger.info("Inizializzazione DB...")
    await init_db()

    errorlog.install()

    acquired = await _acquire_bot(token)
    if not acquired:
        logger.error("Impossibile avviare: bot Telegram non acquisibile")
        return

    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    notifier.set_bot(app.bot)

    mods = cfg.get("modules", {})
    if (mods.get("agenda", False) or mods.get("food_diary", False)
            or mods.get("news", False) or mods.get("bollette", False)
            or mods.get("economia", False)):
        await scheduler.start(app.bot)
    if mods.get("food_diary", False):
        scheduler.schedule_food_jobs(app.bot)
    # seed bollette da HA (una-tantum) prima di pubblicare i sensori MQTT
    if mods.get("bollette", False):
        try:
            from modules import bollette as _boll
            n = await _boll.seed_from_ha()
            if n:
                logger.info("Bollette: seed iniziale da HA (%d mesi importati).", n)
        except Exception as e:
            logger.warning("Bollette seed fallito: %s", e)
    # MQTT serve a food_diary, bollette ed economia
    if (mods.get("food_diary", False) or mods.get("bollette", False)
            or mods.get("economia", False)):
        await mqtt_pub.start()
    if mods.get("food_diary", False):
        scheduler.schedule_mqtt_refresh()
    # refresh notturno economia/bollette (cavallo del mese)
    if mods.get("economia", False) or mods.get("bollette", False):
        scheduler.schedule_econ_refresh()
    if mods.get("news", False):
        await scheduler.load_briefings()

    web_runner = await webpanel.start()

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
    mqtt_pub.stop()
    scheduler.shutdown()
    try:
        await web_runner.cleanup()
    except Exception:
        pass
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    await _release_bot(token)
    logger.info("HARIA fermata. Bot Telegram rilasciato.")


if __name__ == "__main__":
    asyncio.run(main())
