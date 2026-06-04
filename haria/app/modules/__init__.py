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
from . import agenda, web_search, food_diary, multi_user, bollette

ALL = [agenda, web_search, food_diary, multi_user, bollette]


def _enabled() -> list:
    mods = cfg.get("modules", {})
    return [m for m in ALL if mods.get(m.NAME, False)]


def tools() -> list[dict]:
    """Tool di tutti i moduli abilitati."""
    return [t for m in _enabled() for t in m.TOOLS]


def prompt() -> str:
    """Frammenti system prompt dei moduli abilitati, concatenati.

    Oltre a PROMPT statico, se un modulo espone `dynamic_prompt()` (callable)
    il suo output viene aggiunto: serve per contenuti che cambiano a runtime
    (es. diete caricate da /config senza riavvio).
    """
    out = []
    for m in _enabled():
        out.append(m.PROMPT)
        fn = getattr(m, "dynamic_prompt", None)
        if callable(fn):
            try:
                out.append(fn())
            except Exception:
                pass
    return "".join(out)


def owns(name: str) -> bool:
    """True se un modulo abilitato possiede il tool `name`."""
    return any(any(t["name"] == name for t in m.TOOLS) for m in _enabled())


async def dispatch(name: str, inputs: dict, user_id: str) -> str | None:
    """Esegue il tool nel modulo che lo possiede. None se nessuno lo possiede."""
    for m in _enabled():
        if any(t["name"] == name for t in m.TOOLS):
            return await m.handle(name, inputs, user_id)
    return None
