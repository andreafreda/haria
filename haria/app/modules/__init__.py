"""Registry moduli HARIA.

Ogni modulo espone:
  NAME: str                         chiave in config.modules
  TOOLS: list[dict]                 schema tool Anthropic
  PROMPT: str                       frammento system prompt (quando abilitato)
  async handle(name, inputs, user_id) -> str   esegue i propri tool

Per aggiungere un modulo: crea app/modules/<x>.py col contratto sopra e
aggiungilo a ALL qui.
"""
import config as cfg
from . import reminders, websearch, food_diary, messaging

ALL = [reminders, websearch, food_diary, messaging]


def _enabled() -> list:
    mods = cfg.get("modules", {})
    return [m for m in ALL if mods.get(m.NAME, False)]


def tools() -> list[dict]:
    """Tool di tutti i moduli abilitati."""
    return [t for m in _enabled() for t in m.TOOLS]


def prompt() -> str:
    """Frammenti system prompt dei moduli abilitati, concatenati."""
    return "".join(m.PROMPT for m in _enabled())


def owns(name: str) -> bool:
    """True se un modulo abilitato possiede il tool `name`."""
    return any(any(t["name"] == name for t in m.TOOLS) for m in _enabled())


async def dispatch(name: str, inputs: dict, user_id: str) -> str | None:
    """Esegue il tool nel modulo che lo possiede. None se nessuno lo possiede."""
    for m in _enabled():
        if any(t["name"] == name for t in m.TOOLS):
            return await m.handle(name, inputs, user_id)
    return None
