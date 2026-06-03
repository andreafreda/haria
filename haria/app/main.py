import asyncio
import logging
import os
from memory import init_db
from telegram_handler import build_app

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("haria")


async def main():
    token = os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_TOKEN non configurato")
        return

    logger.info("Inizializzazione DB...")
    await init_db()

    logger.info("Avvio HARIA...")
    app = build_app(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("HARIA attiva. In attesa di messaggi.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
