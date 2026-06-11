"""Test publish_economia: verifica topic/payload pubblicati con broker fake."""
import json

import mqtt_pub


class _FakeClient:
    def __init__(self):
        self.published = {}  # topic -> payload (ultimo)

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _decode(fake, topic):
    raw = fake.published.get(topic)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


async def _setup(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(mqtt_pub, "_enabled", True)
    monkeypatch.setattr(mqtt_pub, "_client", fake)
    return fake


async def test_publish_economia_saldi(db, monkeypatch):
    fake = await _setup(monkeypatch)
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "alimentari", "frutta")
    await db.add_transazione("postepay_andrea", "2026-06-02", -30, "trasporti", "bus")

    await mqtt_pub.publish_economia()

    assert _decode(fake, "haria/economia/saldo/contanti_andrea") == -20.0
    assert _decode(fake, "haria/economia/saldo/postepay_andrea") == -30.0
    assert _decode(fake, "haria/economia/saldo_totale") == -50.0
    # saldo aggregato per intestatario
    assert _decode(fake, "haria/economia/saldo_intestatario/andrea") == -50.0
    # discovery pubblicata
    assert "homeassistant/sensor/haria_econ_saldo_contanti_andrea/config" in fake.published
    assert "homeassistant/sensor/haria_econ_saldo_totale/config" in fake.published


async def test_publish_economia_spese_mese(db, monkeypatch):
    fake = await _setup(monkeypatch)
    from datetime import date
    oggi = date.today().isoformat()
    await db.add_transazione("contanti_andrea", oggi, -40, "alimentari", "spesa")
    await db.add_transazione("contanti_andrea", oggi, 100, "stipendio", "")

    await mqtt_pub.publish_economia()

    assert _decode(fake, "haria/economia/spese_mese/state") == 40.0
    attr = _decode(fake, "haria/economia/spese_mese/attr")
    assert attr["per_categoria"]["alimentari"] == -40
    assert attr["entrate"] == 100


async def test_publish_economia_budget(db, monkeypatch):
    fake = await _setup(monkeypatch)
    from datetime import date
    oggi = date.today().isoformat()
    await db.set_budget("alimentari", 300)
    await db.add_transazione("contanti_andrea", oggi, -150, "alimentari", "spesa")

    await mqtt_pub.publish_economia()

    assert _decode(fake, "haria/economia/budget/alimentari/state") == 50.0
    attr = _decode(fake, "haria/economia/budget/alimentari/attr")
    assert attr["budget"] == 300
    assert attr["speso"] == 150
    assert attr["residuo"] == 150
    assert attr["sforato"] is False
    assert "homeassistant/sensor/haria_econ_budget_alimentari/config" in fake.published


async def test_publish_economia_obiettivi(db, monkeypatch):
    fake = await _setup(monkeypatch)
    await db.set_obiettivo("vacanze", 1000)
    await db.accantona("vacanze", 250)

    await mqtt_pub.publish_economia()

    assert _decode(fake, "haria/economia/obiettivo/vacanze/state") == 25.0
    attr = _decode(fake, "haria/economia/obiettivo/vacanze/attr")
    assert attr["target"] == 1000
    assert attr["accantonato"] == 250
    assert attr["residuo"] == 750
    assert attr["raggiunto"] is False
    assert "homeassistant/sensor/haria_econ_obiettivo_vacanze/config" in fake.published


async def test_publish_economia_disabled_noop(db, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(mqtt_pub, "_enabled", False)
    monkeypatch.setattr(mqtt_pub, "_client", fake)
    await mqtt_pub.publish_economia()
    assert fake.published == {}
