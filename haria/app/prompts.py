"""Caricatore dei prompt esternalizzati.

I testi dei prompt (system prompt, istruzioni dei moduli, prompt di riassunto,
ecc.) vivono in file di testo dentro la cartella ``prompts/`` accanto a questo
modulo. Tenerli fuori dal codice permette di modificarli senza toccare la logica
Python e di rileggerli a colpo d'occhio.

Convenzione placeholder: ``{{nome}}`` (doppie graffe) — così non collidono con
le graffe singole presenti nel testo dei prompt (es. esempi JSON ``{...}``).
Usa ``get("file", nome="valore")`` per sostituirli.
"""
import os
from functools import lru_cache

_DIR = os.path.join(os.path.dirname(__file__), "prompts")


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    with open(os.path.join(_DIR, f"{name}.txt"), encoding="utf-8") as f:
        # rstrip dei soli newline finali: preserva un eventuale newline iniziale
        # (i prompt dei moduli iniziano con "\n- ...").
        return f.read().rstrip("\n")


def get(_file: str, **kw) -> str:
    """Legge il prompt ``_file`` e sostituisce i placeholder ``{{chiave}}``."""
    txt = _read(_file)
    for k, v in kw.items():
        txt = txt.replace("{{" + k + "}}", str(v))
    return txt
