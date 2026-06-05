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
