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


async def call_service(domain: str, service: str, data: dict) -> dict:
    url = f"{_base()}/api/services/{domain}/{service}"
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
