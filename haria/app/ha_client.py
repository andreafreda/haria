import aiohttp
import logging
import os

logger = logging.getLogger(__name__)

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
TIMEOUT = aiohttp.ClientTimeout(total=10)


def _headers():
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }


async def get_states(entity_ids: list[str] | None = None) -> list[dict]:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(f"{HA_URL}/api/states", headers=_headers()) as resp:
                if resp.status == 401:
                    raise PermissionError("HA token non valido o scaduto")
                resp.raise_for_status()
                states = await resp.json()
    except aiohttp.ClientConnectorError:
        raise ConnectionError(f"HA non raggiungibile su {HA_URL}")
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
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{HA_URL}/api/services/{domain}/{service}",
                headers=_headers(),
                json=data,
            ) as resp:
                if resp.status == 401:
                    raise PermissionError("HA token non valido o scaduto")
                resp.raise_for_status()
                return await resp.json()
    except aiohttp.ClientConnectorError:
        raise ConnectionError(f"HA non raggiungibile su {HA_URL}")
    except TimeoutError:
        raise TimeoutError("HA non risponde (timeout 10s)")
