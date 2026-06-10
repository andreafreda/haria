"""Smoke test: valida che il setup pytest + fixture db funzioni sul DB reale."""


async def test_db_init_and_bolletta_roundtrip(db):
    await db.set_bolletta("corrente", "kwh", 2026, 3, 280)
    csv = await db.get_bolletta_csv("corrente", "kwh", 2026)
    values = csv.split(",")
    assert len(values) == 12
    assert values[2] == "280"  # marzo = indice 2
