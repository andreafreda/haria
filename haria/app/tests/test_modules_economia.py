import json

from modules import economia


async def test_add_transazione_spesa_default_contanti(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 20, "categoria": "alimentari", "descrizione": "frutta",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["conto"] == "contanti"
    assert data["importo"] == 20
    assert data["saldo_aggiornato"] == -20


async def test_add_transazione_entrata_conto_alias(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "entrata", "importo": 1500, "categoria": "stipendio",
        "conto": "conto corrente", "data": "2026-06-01",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["conto"] == "bancoposta"
    assert data["saldo_aggiornato"] == 1500


async def test_add_transazione_conto_sconosciuto(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "varie", "conto": "revolut",
    }, "u1")
    assert "non riconosciuto" in out


async def test_add_transazione_tipo_invalido(db):
    out = await economia.handle("add_transazione", {
        "tipo": "boh", "importo": 10, "categoria": "varie",
    }, "u1")
    assert "Tipo non valido" in out


async def test_add_transazione_importo_zero(db):
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 0, "categoria": "varie",
    }, "u1")
    assert "non può essere zero" in out


async def test_add_transazione_categoria_mancante(db):
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "  ",
    }, "u1")
    assert "Categoria mancante" in out


async def test_unknown_tool(db):
    out = await economia.handle("foo", {}, "u1")
    assert "Tool sconosciuto" in out


def _wire(monkeypatch, db):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)
    monkeypatch.setattr(economia, "get_saldi", db.get_saldi)
    monkeypatch.setattr(economia, "riepilogo_spese", db.riepilogo_spese)
    monkeypatch.setattr(economia, "normalize_categoria", db.normalize_categoria)
    monkeypatch.setattr(economia, "list_categorie", db.list_categorie)
    monkeypatch.setattr(economia, "rename_categoria", db.rename_categoria)
    monkeypatch.setattr(economia, "merge_categoria", db.merge_categoria)
    monkeypatch.setattr(economia, "reset_economia", db.reset_economia)


async def test_get_saldo_conto_singolo(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("postepay", "2026-06-01", -5, "spesa", "caffe")
    out = await economia.handle("get_saldo", {"conto": "postepay"}, "u1")
    data = json.loads(out)
    assert data["conto"] == "postepay"
    assert data["saldo"] == -5


async def test_get_saldo_tutti(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti", "2026-06-01", -20, "spesa", "frutta")
    out = await economia.handle("get_saldo", {}, "u1")
    data = json.loads(out)
    assert "saldi" in data
    assert data["totale"] == -20


async def test_get_saldo_conto_sconosciuto(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("get_saldo", {"conto": "revolut"}, "u1")
    assert "non riconosciuto" in out


async def test_riepilogo_spese_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti", "2026-06-02", -200, "alimentari", "spesa")
    out = await economia.handle("riepilogo_spese", {
        "data_da": "2026-06-01", "data_a": "2026-06-30",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["uscite"] == -200
    assert data["conto"] == "tutti"
    assert data["periodo"]["da"] == "2026-06-01"


async def test_riepilogo_spese_tool_conto_sconosciuto(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("riepilogo_spese", {"conto": "revolut"}, "u1")
    assert "non riconosciuto" in out


async def test_add_transazione_normalizza_categoria(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "ALIMENTARI", "descrizione": "x",
    }, "u1")
    data = json.loads(out)
    assert data["categoria"] == "alimentari"


async def test_gestisci_categorie_lista(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "lista"}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert "alimentari" in data["categorie"]


async def test_gestisci_categorie_rinomina(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {
        "azione": "rinomina", "da": "svago", "a": "Tempo Libero",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["a"] == "tempo libero"


async def test_gestisci_categorie_rinomina_args_mancanti(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "rinomina", "da": "svago"}, "u1")
    assert "servono" in out


async def test_gestisci_categorie_unisci(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.normalize_categoria("cibo")
    out = await economia.handle("gestisci_categorie", {
        "azione": "unisci", "da": "cibo", "a": "alimentari",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert "cibo" not in await db.list_categorie()


async def test_gestisci_categorie_azione_invalida(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "boh"}, "u1")
    assert "Azione non valida" in out


async def test_reset_economia_anteprima_senza_confirm(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("reset_economia", {}, "u1")
    data = json.loads(out)
    assert data["ok"] is False
    assert data["conferma_richiesta"] is True
    # nulla cancellato
    assert await db.get_saldo("contanti") == -10


async def test_reset_economia_confirm(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("reset_economia", {"confirm": True}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["transazioni_cancellate"] == 1
    assert await db.get_saldo("contanti") == 0


async def test_dynamic_prompt_include_categorie(db):
    # db fixture ha gia' inizializzato memory.DB_PATH al test db con le categorie seed
    out = economia.dynamic_prompt()
    assert "alimentari" in out
    assert "ECONOMIA categorie" in out
