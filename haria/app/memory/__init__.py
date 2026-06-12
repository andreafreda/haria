"""HARIA persistence — facade del package memory (split per dominio).

API identica al vecchio memory.py: `from memory import X` continua a funzionare.
Lo schema e lo stato condiviso (DB_PATH, _FTS_OK, init_db) vivono in core. I
sottomoduli leggono `core.DB_PATH` a runtime; il facade espone DB_PATH/_FTS_OK
via __getattr__ delegando a core, così restano monkeypatchabili dai test
(`memory.core.DB_PATH`) e i consumatori che leggono `memory.DB_PATH` vedono
sempre il valore vivo.
"""
from . import core
from .core import *  # noqa: F401,F403
from .misc import *  # noqa: F401,F403
from .food import *  # noqa: F401,F403
from .bollette import *  # noqa: F401,F403
from .econ import *  # noqa: F401,F403
from .core import MAX_HISTORY, SUMMARY_BATCH, CATEGORIA_TRASFERIMENTO  # noqa: F401  costanti stabili

# DB_PATH e _FTS_OK sono stato mutabile (init_db setta _FTS_OK; i test
# monkeypatchano DB_PATH): non tenerne una copia statica nel facade, delegare
# live a core via __getattr__.
del DB_PATH  # noqa: F821  (creato da 'from .core import *', ora delegato a core)


def __getattr__(name):
    if name in ("DB_PATH", "_FTS_OK"):
        return getattr(core, name)
    raise AttributeError(f"module 'memory' has no attribute {name!r}")
