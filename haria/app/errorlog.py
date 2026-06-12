"""Cattura errori/eccezioni: persiste su DB e manda notifica push via HA.

Un logging.Handler agganciato al root logger intercetta ogni record di livello
>= ERROR da qualsiasi modulo, lo salva in error_log (visibile nel pannello
/logs) e invia una notifica all'app Home Assistant (telefono).

Anti-loop: ignora i log emessi da questo modulo e da ha_client (l'invio della
notifica passa da ha_client.call_service, che a sua volta logga errori).
"""
import asyncio
import logging
import time
import traceback as _tb
import config as cfg
from memory import add_error_log
from ha_client import call_service

logger = logging.getLogger(__name__)

_loop = None
_IGNORE = {__name__, "ha_client"}
_last_notify: dict[tuple, float] = {}
_THROTTLE_S = 600


def _notify_target() -> tuple[str, str]:
    """(domain, service) per la notifica. Preferisci notify_service del primo
    utente con uno configurato; fallback persistent_notification.create."""
    for u in cfg.get("users", []) or []:
        svc = (u.get("notify_service") or "").strip()
        if svc:
            return ("notify", svc.replace("notify.", ""))
    return ("persistent_notification", "create")


async def _dispatch(source: str, message: str, tb: str):
    try:
        await add_error_log(source, "ERROR", message, tb)
    except Exception:
        pass
    # throttle push: max 1 notifica per (source, message) ogni 10 min. Il log su
    # DB resta sempre (serve a /logs); si throttla solo la notifica sul telefono.
    key = (source, message[:120])
    now = time.monotonic()
    if now - _last_notify.get(key, 0) < _THROTTLE_S:
        return
    _last_notify[key] = now
    if len(_last_notify) > 200:
        cutoff = now - _THROTTLE_S
        for k in [k for k, t in _last_notify.items() if t < cutoff]:
            del _last_notify[k]
    try:
        dom, svc = _notify_target()
        short = message if len(message) <= 200 else message[:200] + "…"
        await call_service(dom, svc, {"title": f"HARIA errore: {source}", "message": short})
    except Exception:
        pass


class DBNotifyHandler(logging.Handler):
    def emit(self, record):
        if record.levelno < logging.ERROR or record.name in _IGNORE:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        tb = "".join(_tb.format_exception(*record.exc_info)) if record.exc_info else ""
        if _loop is None:
            return
        try:
            _loop.call_soon_threadsafe(
                lambda: asyncio.create_task(_dispatch(record.name, msg, tb))
            )
        except RuntimeError:
            pass


def install():
    """Aggancia l'handler al root logger. Chiamare con il loop asyncio attivo."""
    global _loop
    _loop = asyncio.get_running_loop()
    handler = DBNotifyHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)
    logger.info("Error log + notifiche errori installati")
