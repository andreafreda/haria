import pytest

import econ_def


async def test_seed_conti_default(db):
    conti = await db.list_conti()
    nomi = {c["nome"] for c in conti}
    assert nomi == set(econ_def.CONTI.keys())
    for c in conti:
        assert c["tipo"] == econ_def.CONTI[c["nome"]]["tipo"]
        assert c["saldo_iniziale"] == 0
        assert c["attivo"] is True


async def test_get_conto_unknown(db):
    assert await db.get_conto("inesistente") is None


async def test_add_conto_custom(db):
    cid = await db.add_conto("revolut", "carta", saldo_iniziale=50)
    assert cid > 0
    c = await db.get_conto("revolut")
    assert c["tipo"] == "carta"
    assert c["saldo_iniziale"] == 50


async def test_add_conto_idempotente(db):
    id1 = await db.add_conto("revolut", "carta")
    id2 = await db.add_conto("revolut", "carta")
    assert id1 == id2


async def test_saldo_iniziale_no_transazioni(db):
    assert await db.get_saldo("contanti") == 0


async def test_add_transazione_e_saldo(db):
    await db.add_transazione("contanti", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("contanti", "2026-06-02", 100, "entrata", "stipendio")
    assert await db.get_saldo("contanti") == 80


async def test_add_transazione_conto_sconosciuto(db):
    with pytest.raises(ValueError):
        await db.add_transazione("non_esiste", "2026-06-01", -10, "spesa", "x")


async def test_get_saldo_conto_sconosciuto(db):
    with pytest.raises(ValueError):
        await db.get_saldo("non_esiste")


async def test_list_transazioni_filtri(db):
    await db.add_transazione("contanti", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("postepay", "2026-06-02", -5, "spesa", "caffe")
    await db.add_transazione("contanti", "2026-06-03", -10, "regali", "fiori")

    tutte = await db.list_transazioni()
    assert len(tutte) == 3

    solo_contanti = await db.list_transazioni(conto="contanti")
    assert len(solo_contanti) == 2
    assert all(t["conto"] == "contanti" for t in solo_contanti)

    solo_spesa = await db.list_transazioni(categoria="spesa")
    assert len(solo_spesa) == 2

    range_date = await db.list_transazioni(data_da="2026-06-02", data_a="2026-06-02")
    assert len(range_date) == 1
    assert range_date[0]["descrizione"] == "caffe"


async def test_list_transazioni_ordine_desc(db):
    await db.add_transazione("contanti", "2026-06-01", -1, "spesa", "a")
    await db.add_transazione("contanti", "2026-06-03", -1, "spesa", "b")
    await db.add_transazione("contanti", "2026-06-02", -1, "spesa", "c")

    righe = await db.list_transazioni(conto="contanti")
    assert [r["descrizione"] for r in righe] == ["b", "c", "a"]


async def test_delete_transazione(db):
    tid = await db.add_transazione("contanti", "2026-06-01", -20, "spesa", "frutta")
    assert await db.get_saldo("contanti") == -20

    ok = await db.delete_transazione(tid)
    assert ok is True
    assert await db.get_saldo("contanti") == 0

    assert await db.delete_transazione(tid) is False


async def test_get_saldi_tutti(db):
    await db.add_transazione("contanti", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("postepay", "2026-06-01", -5, "spesa", "caffe")
    saldi = {s["conto"]: s["saldo"] for s in await db.get_saldi()}
    assert saldi["contanti"] == -20
    assert saldi["postepay"] == -5
    assert saldi["bancoposta"] == 0
    assert set(saldi.keys()) == set(econ_def.CONTI.keys())


async def test_riepilogo_spese_totali_e_categorie(db):
    await db.add_transazione("contanti", "2026-06-01", 1500, "stipendio", "")
    await db.add_transazione("contanti", "2026-06-02", -200, "alimentari", "spesa")
    await db.add_transazione("postepay", "2026-06-03", -50, "alimentari", "frutta")
    await db.add_transazione("postepay", "2026-06-04", -80, "trasporti", "benzina")

    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 1500
    assert rep["uscite"] == -330
    assert rep["netto"] == 1170
    # ordinate per importo crescente (piu' negativo = piu' speso prima)
    assert rep["per_categoria"][0] == {"categoria": "alimentari", "totale": -250}
    assert {c["categoria"] for c in rep["per_categoria"]} == {"alimentari", "trasporti"}


async def test_riepilogo_spese_filtro_periodo_e_conto(db):
    await db.add_transazione("contanti", "2026-05-31", -10, "alimentari", "x")
    await db.add_transazione("contanti", "2026-06-15", -20, "alimentari", "y")
    await db.add_transazione("postepay", "2026-06-15", -99, "svago", "z")

    rep = await db.riepilogo_spese(data_da="2026-06-01", data_a="2026-06-30", conto="contanti")
    assert rep["uscite"] == -20
    assert len(rep["per_categoria"]) == 1
    assert rep["per_categoria"][0]["categoria"] == "alimentari"


async def test_riepilogo_spese_solo_entrate(db):
    await db.add_transazione("contanti", "2026-06-01", 1500, "stipendio", "")
    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 1500.0
    assert rep["uscite"] == 0.0
    assert isinstance(rep["uscite"], float)
    assert rep["netto"] == 1500.0
    assert rep["per_categoria"] == []


async def test_riepilogo_spese_vuoto(db):
    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 0
    assert rep["uscite"] == 0
    assert rep["netto"] == 0
    assert rep["per_categoria"] == []


async def test_amount_rounding(db):
    await db.add_transazione("contanti", "2026-06-01", -19.995, "spesa", "x")
    await db.add_transazione("contanti", "2026-06-02", 0.001, "entrata", "y")
    assert await db.get_saldo("contanti") == -19.99


# ---- categorie ----

async def test_categorie_seed_default(db):
    cats = await db.list_categorie()
    assert set(econ_def.CATEGORIE_DEFAULT) <= set(cats)


async def test_normalize_categoria_match_case_insensitive(db):
    # 'alimentari' e' seedata
    assert await db.normalize_categoria("Alimentari") == "alimentari"
    assert await db.normalize_categoria("  ALIMENTARI ") == "alimentari"
    # nessuna nuova categoria creata
    cats = await db.list_categorie()
    assert cats.count("alimentari") == 1


async def test_normalize_categoria_nuova(db):
    prima = set(await db.list_categorie())
    out = await db.normalize_categoria("Criptovalute")
    assert out == "criptovalute"
    dopo = set(await db.list_categorie())
    assert dopo - prima == {"criptovalute"}


async def test_normalize_categoria_vuota(db):
    assert await db.normalize_categoria("   ") == "varie"
    assert await db.normalize_categoria(None) == "varie"


async def test_rename_categoria_propaga(db):
    await db.add_transazione("contanti", "2026-06-01", -10, "alimentari", "x")
    ok = await db.rename_categoria("alimentari", "Cibo")
    assert ok is True
    cats = await db.list_categorie()
    assert "cibo" in cats
    assert "alimentari" not in cats
    righe = await db.list_transazioni(categoria="cibo")
    assert len(righe) == 1


async def test_rename_categoria_inesistente(db):
    assert await db.rename_categoria("nonesiste", "x") is False


async def test_rename_su_esistente_equivale_merge(db):
    await db.add_transazione("contanti", "2026-06-01", -10, "carburante", "x")
    await db.add_transazione("contanti", "2026-06-02", -5, "trasporti", "y")
    ok = await db.rename_categoria("carburante", "trasporti")
    assert ok is True
    cats = await db.list_categorie()
    assert "carburante" not in cats
    assert "trasporti" in cats
    righe = await db.list_transazioni(categoria="trasporti")
    assert len(righe) == 2


async def test_merge_categoria(db):
    await db.normalize_categoria("cibo")
    await db.add_transazione("contanti", "2026-06-01", -10, "cibo", "x")
    await db.add_transazione("contanti", "2026-06-02", -5, "alimentari", "y")
    ok = await db.merge_categoria("cibo", "alimentari")
    assert ok is True
    cats = await db.list_categorie()
    assert "cibo" not in cats
    assert (await db.list_transazioni(categoria="alimentari")).__len__() == 2


async def test_merge_categoria_src_inesistente(db):
    assert await db.merge_categoria("nonesiste", "alimentari") is False


async def test_merge_categoria_dst_creata(db):
    await db.normalize_categoria("vecchia")
    await db.add_transazione("contanti", "2026-06-01", -10, "vecchia", "x")
    ok = await db.merge_categoria("vecchia", "nuovissima")
    assert ok is True
    cats = await db.list_categorie()
    assert "nuovissima" in cats
    assert "vecchia" not in cats


async def test_merge_categoria_su_se_stessa(db):
    assert await db.merge_categoria("alimentari", "alimentari") is False


# ---- reset ----

async def test_reset_economia_solo_transazioni(db):
    await db.add_transazione("contanti", "2026-06-01", -10, "alimentari", "x")
    await db.add_transazione("postepay", "2026-06-02", -5, "svago", "y")
    res = await db.reset_economia()
    assert res["transazioni_cancellate"] == 2
    assert res["categorie_resettate"] == 0
    assert res["saldi_azzerati"] == 0
    assert await db.get_saldo("contanti") == 0
    # categorie restano
    assert "alimentari" in await db.list_categorie()


async def test_reset_economia_categorie(db):
    await db.normalize_categoria("categoria_custom")
    assert "categoria_custom" in await db.list_categorie()
    res = await db.reset_economia(reset_categorie=True)
    assert res["categorie_resettate"] > 0
    cats = await db.list_categorie()
    assert "categoria_custom" not in cats
    assert set(econ_def.CATEGORIE_DEFAULT) == set(cats)


async def test_reset_economia_saldi(db):
    await db.add_conto("revolut", "carta", saldo_iniziale=100)
    res = await db.reset_economia(reset_saldi=True)
    assert res["saldi_azzerati"] == 1
    assert (await db.get_conto("revolut"))["saldo_iniziale"] == 0


async def test_reset_economia_vuoto(db):
    res = await db.reset_economia()
    assert res["transazioni_cancellate"] == 0
