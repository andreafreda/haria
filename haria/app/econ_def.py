"""Definizione conti economia domestica — UNICO punto di estensione.

Aggiungere un conto (es. nuova carta) = aggiungere una entry in CONTI.
Seed automatico al primo avvio (memory.init_db). Niente altro da toccare.

Ogni conto ha:
  label    nome leggibile (dashboard/notifiche)
  tipo     banca|carta|wallet|contanti (per icona/dashboard)
  icon     mdi icon
  aliases  sinonimi accettati in input (per riconoscere il conto dal testo)
"""

CONTI: dict[str, dict] = {
    "bancoposta": {
        "label": "BancoPosta",
        "tipo": "banca",
        "icon": "mdi:bank",
        "aliases": ["conto", "conto corrente", "poste", "bancoposta"],
    },
    "postepay": {
        "label": "PostePay",
        "tipo": "carta",
        "icon": "mdi:credit-card",
        "aliases": ["carta", "postepay"],
    },
    "paypal": {
        "label": "PayPal",
        "tipo": "wallet",
        "icon": "mdi:paypal",
        "aliases": ["paypal"],
    },
    "contanti": {
        "label": "Contanti",
        "tipo": "contanti",
        "icon": "mdi:cash",
        "aliases": ["cash", "soldi", "contanti"],
    },
}


# Categorie iniziali (l'utente può aggiungerne/rinominarle/unirle via chat).
CATEGORIE_DEFAULT: list[str] = [
    "alimentari",
    "ristoranti",
    "trasporti",
    "carburante",
    "bollette",
    "casa",
    "salute",
    "svago",
    "shopping",
    "abbonamenti",
    "istruzione",
    "regali",
    "tasse",
    "prelievo contanti",
    "stipendio",
    "entrate varie",
]


def norm_conto(c: str) -> str | None:
    """Normalizza testo libero alla chiave conto (o None)."""
    t = (c or "").strip().lower()
    if t in CONTI:
        return t
    for key, d in CONTI.items():
        if t in d.get("aliases", []):
            return key
    return None


def label(conto: str) -> str:
    return CONTI[conto]["label"]


def icon(conto: str) -> str:
    return CONTI[conto]["icon"]


def tipo(conto: str) -> str:
    return CONTI[conto]["tipo"]
