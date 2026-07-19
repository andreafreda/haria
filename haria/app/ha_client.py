"""Client Home Assistant: chiamate REST e WebSocket alla Supervisor/Core API
(stati entità, call_service, eventi calendario) usate dal motore e dai moduli."""
import aiohttp
import logging
import config as cfg

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)


def _headers():
    return {
        "Authorization": f"Bearer {cfg.get('ha_token', '')}",
        "Content-Type": "application/json",
    }


def _base():
    return cfg.get("ha_url", "http://homeassistant:8123")


async def get_states(entity_ids: list[str] | None = None) -> list[dict]:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(f"{_base()}/api/states", headers=_headers()) as resp:
                if resp.status == 401:
                    raise PermissionError("HA token non valido o scaduto")
                resp.raise_for_status()
                states = await resp.json()
    except aiohttp.ClientConnectorError:
        raise ConnectionError(f"HA non raggiungibile su {_base()}")
    except TimeoutError:
        raise TimeoutError("HA non risponde (timeout 10s)")

    if entity_ids:
        states = [s for s in states if s["entity_id"] in entity_ids]
    return [
        {
            "entity_id": s["entity_id"],
            "state": s["state"],
            "attributes": s.get("attributes", {}),
        }
        for s in states
    ]


async def get_calendar_events(entity_id: str, start_iso: str, end_iso: str) -> list[dict]:
    """Eventi di un calendario via REST /api/calendars/<entity> (include uid,
    a differenza del servizio calendar.get_events)."""
    url = f"{_base()}/api/calendars/{entity_id}?start={start_iso}&end={end_iso}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers=_headers()) as resp:
                if resp.status == 401:
                    raise PermissionError("HA token non valido o scaduto")
                resp.raise_for_status()
                data = await resp.json()
    except aiohttp.ClientConnectorError:
        raise ConnectionError(f"HA non raggiungibile su {_base()}")
    except TimeoutError:
        raise TimeoutError("HA non risponde (timeout 10s)")
    return data if isinstance(data, list) else []


async def ws_command(payload: dict) -> dict:
    """Esegue un comando WebSocket HA (per API non REST: es. calendar/event/delete,
    calendar/event/update, calendar/event/list). Ritorna il campo `result`."""
    base = _base().rstrip("/")
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.ws_connect(ws_url) as ws:
                await ws.receive_json()  # auth_required
                await ws.send_json({"type": "auth", "access_token": cfg.get("ha_token", "")})
                ack = await ws.receive_json()
                if ack.get("type") != "auth_ok":
                    raise PermissionError("HA WS auth fallita")
                await ws.send_json({"id": 1, **payload})
                while True:
                    msg = await ws.receive_json()
                    if msg.get("type") == "result" and msg.get("id") == 1:
                        if not msg.get("success"):
                            err = (msg.get("error") or {}).get("message", "WS error")
                            raise RuntimeError(f"HA WS: {err}")
                        return msg.get("result") or {}
    except aiohttp.ClientConnectorError:
        raise ConnectionError(f"HA non raggiungibile su {_base()}")
    except TimeoutError:
        raise TimeoutError("HA non risponde (timeout WS)")


async def call_service(domain: str, service: str, data: dict,
                       return_response: bool = False) -> dict:
    try:
        return await _post_service(domain, service, data, return_response)
    except aiohttp.ClientResponseError as e:
        # Alcuni climate non implementano turn_off/turn_on (HA risponde 400
        # ServiceNotSupported). Ripieghiamo su set_hvac_mode, che ogni climate
        # con la modalità corrispondente accetta. Il percorso normale resta
        # invariato: il fallback scatta solo sull'errore.
        fallback_mode = _climate_hvac_fallback(domain, service, e.status)
        if fallback_mode is None:
            raise
        logger.info("climate.%s non supportato; ripiego su set_hvac_mode=%s",
                    service, fallback_mode)
        return await _post_service(
            "climate", "set_hvac_mode", {**data, "hvac_mode": fallback_mode},
            return_response)


def _climate_hvac_fallback(domain: str, service: str, status: int) -> str | None:
    """Modalità hvac con cui riprovare un turn_off/turn_on climate fallito.

    Ritorna None quando non c'è un ripiego sensato (dominio/servizio diverso,
    errore non 400): in quel caso il chiamante rilancia l'errore originale.
    turn_on non ha una modalità univoca, quindi usiamo 'auto', presente sulla
    maggior parte dei climate.
    """
    # HA segnala "servizio non supportato dall'entità" con 400, ma alcune
    # versioni/entità lo propagano come 500. Copriamo entrambi.
    if status not in (400, 500) or domain != "climate":
        return None
    return {"turn_off": "off", "turn_on": "auto"}.get(service)


async def _post_service(domain: str, service: str, data: dict,
                        return_response: bool = False) -> dict:
    """POST grezzo a /api/services; gestisce auth, connessione e timeout.

    Alza aiohttp.ClientResponseError sugli status di errore HA (es. 400 quando
    un servizio non è supportato dall'entità), così call_service può decidere
    se ripiegare o propagare.
    """
    url = f"{_base()}/api/services/{domain}/{service}"
    if return_response:
        url += "?return_response"
    logger.info("call_service %s/%s data=%s", domain, service, data)
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, headers=_headers(), json=data) as resp:
                logger.info("call_service response: %s", resp.status)
                if resp.status == 401:
                    raise PermissionError("HA token non valido o scaduto")
                resp.raise_for_status()
                return await resp.json()
    except aiohttp.ClientConnectorError as e:
        logger.error("HA non raggiungibile: %s", e)
        raise ConnectionError(f"HA non raggiungibile su {_base()}")
    except TimeoutError:
        raise TimeoutError("HA non risponde (timeout 10s)")
