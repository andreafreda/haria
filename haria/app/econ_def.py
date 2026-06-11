"""Definizione conti economia domestica — UNICO punto di estensione.

Gestione familiare con profilazione per intestatario:
  - BancoPosta = conto unico cointestato (intestatario "famiglia")
  - PostePay / PayPal / Contanti = per membro (carte/wallet separati)

I conti sono generati da MEMBRI + i template per-membro. Seed automatico al
primo avvio (memory.init_db). Aggiungere un membro = aggiungerlo a MEMBRI.

Ogni conto (valore in CONTI) ha:
  label         nome leggibile (dashboard/notifiche)
  tipo          banca|carta|wallet|contanti (per icona/dashboard)
  icon          mdi icon
  intestatario  "famiglia" oppure nome membro (per report per-persona)
  aliases       sinonimi accettati in input (per riconoscere il conto dal testo)
"""

# Membri della famiglia (chiavi minuscole; coincidono con i nomi in config.users)
MEMBRI: list[str] = ["andrea", "marina"]


def _conti() -> dict[str, dict]:
    conti: dict[str, dict] = {
        "bancoposta": {
            "label": "BancoPosta",
            "tipo": "banca",
            "icon": "mdi:bank",
            "intestatario": "famiglia",
            "aliases": ["conto", "conto corrente", "poste", "bancoposta",
                        "banco posta", "conto comune"],
        },
    }
    for m in MEMBRI:
        cap = m.capitalize()
        conti[f"postepay_{m}"] = {
            "label": f"PostePay {cap}",
            "tipo": "carta",
            "icon": "mdi:credit-card",
            "intestatario": m,
            "aliases": [f"postepay {m}", f"postepay di {m}", f"carta {m}",
                        f"carta di {m}", f"la postepay di {m}"],
        }
        conti[f"paypal_{m}"] = {
            "label": f"PayPal {cap}",
            "tipo": "wallet",
            "icon": "mdi:paypal",
            "intestatario": m,
            "aliases": [f"paypal {m}", f"paypal di {m}"],
        }
        conti[f"contanti_{m}"] = {
            "label": f"Contanti {cap}",
            "tipo": "contanti",
            "icon": "mdi:cash",
            "intestatario": m,
            "aliases": [f"contanti {m}", f"contanti di {m}", f"cash {m}",
                        f"soldi {m}"],
        }
    return conti


CONTI: dict[str, dict] = _conti()


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
    "trasferimento",
    "stipendio",
    "entrate varie",
]


def conto_per(tipo_base: str, membro: str) -> str | None:
    """Chiave conto per (tipo_base in {postepay,paypal,contanti}, membro)."""
    key = f"{tipo_base}_{(membro or '').strip().lower()}"
    return key if key in CONTI else None


def norm_conto(c: str, membro: str | None = None) -> str | None:
    """Normalizza testo libero alla chiave conto.

    Se `membro` è dato, risolve i riferimenti generici ("postepay", "contanti")
    al conto di quel membro. 'bancoposta'/'conto' restano famiglia.
    """
    t = (c or "").strip().lower()
    if not t:
        return None
    if t in CONTI:
        return t
    # match alias esatto
    for key, d in CONTI.items():
        if t in d.get("aliases", []):
            return key
    # riferimento generico + contesto membro
    if membro:
        m = membro.strip().lower()
        generic = {
            "postepay": f"postepay_{m}", "carta": f"postepay_{m}",
            "paypal": f"paypal_{m}",
            "contanti": f"contanti_{m}", "cash": f"contanti_{m}",
            "soldi": f"contanti_{m}",
        }
        key = generic.get(t)
        if key and key in CONTI:
            return key
    return None


def label(conto: str) -> str:
    return CONTI[conto]["label"]


def icon(conto: str) -> str:
    return CONTI[conto]["icon"]


def tipo(conto: str) -> str:
    return CONTI[conto]["tipo"]


def intestatario(conto: str) -> str:
    return CONTI[conto]["intestatario"]
