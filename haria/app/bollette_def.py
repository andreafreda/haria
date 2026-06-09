"""Definizione utenze bollette — UNICO punto di estensione.

Aggiungere una utenza (es. telefono) = aggiungere una entry in UTILITIES.
Modulo (modules/bollette.py), pubblicazione MQTT (mqtt_pub.py) e seed si
adeguano automaticamente: nessun altro file da toccare lato HARIA.

Ogni utenza ha:
  label           nome leggibile (dashboard/notifiche)
  consumo_metric  chiave metrica consumo nel DB (es. 'kwh', 'm3', 'giga')
  consumo_unit    unita' mostrata (es. 'kWh', 'm³', 'GB')
  consumo_icon    mdi icon per la metrica consumo
  aliases         sinonimi accettati in input (per riconoscere l'utility dal testo)

La metrica costo ('costo', €) e' implicita per tutte le utenze.
"""

COSTO_METRIC = "costo"
COSTO_UNIT = "€"
COSTO_ICON = "mdi:currency-eur"

UTILITIES: dict[str, dict] = {
    "corrente": {
        "label": "Corrente",
        "consumo_metric": "kwh",
        "consumo_unit": "kWh",
        "consumo_icon": "mdi:lightning-bolt",
        "aliases": ["luce", "elettricità", "elettricita", "energia", "elettrica"],
    },
    "acqua": {
        "label": "Acqua",
        "consumo_metric": "m3",
        "consumo_unit": "m³",
        "consumo_icon": "mdi:water",
        "aliases": ["idrica"],
    },
    "gas": {
        "label": "Gas",
        "consumo_metric": "m3",
        "consumo_unit": "m³",
        "consumo_icon": "mdi:meter-gas",
        "aliases": ["metano"],
    },
    # Esempio per estensione futura — basta scommentare/aggiungere:
    # "telefono": {
    #     "label": "Telefono",
    #     "consumo_metric": "giga",
    #     "consumo_unit": "GB",
    #     "consumo_icon": "mdi:phone",
    #     "aliases": ["cellulare", "mobile", "internet", "fibra", "adsl"],
    # },
}


def norm_utility(u: str) -> str | None:
    """Normalizza testo libero alla chiave utility (o None)."""
    t = (u or "").strip().lower()
    if t in UTILITIES:
        return t
    for key, d in UTILITIES.items():
        if t in d.get("aliases", []):
            return key
    return None


def consumo_metric(util: str) -> str:
    return UTILITIES[util]["consumo_metric"]


def consumo_unit(util: str) -> str:
    return UTILITIES[util]["consumo_unit"]


def metrics(util: str) -> list[tuple]:
    """[(metric, icon, unit_label)] per consumo + costo di una utility."""
    d = UTILITIES[util]
    return [
        (d["consumo_metric"], d["consumo_icon"], d["consumo_unit"]),
        (COSTO_METRIC, COSTO_ICON, COSTO_UNIT),
    ]
