"""Test parser estratti (econ_import) + import_transazioni (dedup)."""
import econ_import


# header BancoPosta con righe di metadata sopra
_BP_ROWS = [
    ["Lista Movimenti", None, None, None, None],
    ["Conto", "001050866464", None, None, None],
    ["Data Contabile", "Data Valuta", "Addebiti (euro)", "Accrediti (euro)", "Descrizione operazioni"],
    ["10/06/2026", "10/06/2026", "20,50", None, "PAGAMENTO POS ESSELUNGA"],
    ["11/06/2026", "11/06/2026", None, "1.500,00", "ACCREDITO STIPENDIO"],
    ["12/06/2026", "12/06/2026", "100,00", None, "PRELIEVO ATM"],
]

_PP_ROWS = [
    ["Lista Movimenti Postepay", None, None, None],
    ["Data Contabile", "Data Valuta", "Importo (euro)", "Descrizione operazioni"],
    ["10/06/2026", "10/06/2026", "-12,50", "GLOVO MILANO"],
    ["11/06/2026", "11/06/2026", "-9,99", "AMAZON"],
    ["12/06/2026", "12/06/2026", "50,00", "RIMBORSO"],
]


def test_detect_format():
    assert econ_import.detect_format(_BP_ROWS) == "bancoposta"
    assert econ_import.detect_format(_PP_ROWS) == "postepay"
    assert econ_import.detect_format([["a", "b"], ["c", "d"]]) is None


def test_parse_bancoposta_importo_firmato():
    res = econ_import.parse(_BP_ROWS)
    assert res["formato"] == "bancoposta"
    assert res["conto"] == "bancoposta"
    movs = res["movimenti"]
    assert len(movs) == 3
    # addebito -> negativo
    assert movs[0]["importo"] == -20.50
    assert movs[0]["data"] == "2026-06-10"
    # accredito -> positivo
    assert movs[1]["importo"] == 1500.00
    # prelievo categorizzato
    assert movs[2]["categoria"] == "prelievo contanti"


def test_parse_postepay():
    res = econ_import.parse(_PP_ROWS)
    assert res["formato"] == "postepay"
    movs = res["movimenti"]
    assert len(movs) == 3
    assert movs[0]["importo"] == -12.50
    assert movs[0]["categoria"] == "ristoranti"   # glovo = delivery
    assert movs[1]["categoria"] == "shopping"     # amazon
    assert movs[2]["importo"] == 50.0


def test_parse_formato_sconosciuto():
    res = econ_import.parse([["x", "y"], ["1", "2"]])
    assert res["formato"] is None
    assert res["movimenti"] == []


def test_parse_date_varianti():
    assert econ_import.parse_date("05/03/2026") == "2026-03-05"
    assert econ_import.parse_date("2026-03-05") == "2026-03-05"
    assert econ_import.parse_date("5-3-2026") == "2026-03-05"
    assert econ_import.parse_date("") is None
    assert econ_import.parse_date(None) is None
    import datetime
    assert econ_import.parse_date(datetime.date(2026, 3, 5)) == "2026-03-05"


def test_to_float_formato_italiano():
    assert econ_import._to_float("1.500,00") == 1500.0
    assert econ_import._to_float("-12,50") == -12.5
    assert econ_import._to_float("20,50") == 20.5
    assert econ_import._to_float("") is None
    assert econ_import._to_float(None) is None


def test_categorize():
    assert econ_import.categorize("RICARICA POSTEPAY A ANDREA", -50) == "trasferimento"
    assert econ_import.categorize("PRELIEVO ATM", -100) == "prelievo contanti"
    assert econ_import.categorize("qualcosa di ignoto", -10) == "altro"
    assert econ_import.categorize("qualcosa di ignoto", 10) == "entrate varie"


def test_row_hash_stabile():
    h1 = econ_import.row_hash("bancoposta", "2026-06-10", -20.5, "POS ESSELUNGA")
    h2 = econ_import.row_hash("bancoposta", "2026-06-10", -20.5, "POS  esselunga ")
    assert h1 == h2  # normalizza spazi/case


def test_scarta_righe_invalide():
    rows = [
        ["Data Contabile", "Data Valuta", "Importo (euro)", "Descrizione operazioni"],
        ["", "", "", ""],                      # vuota -> skip silenzioso
        ["nodate", None, "-5,00", "x"],        # data non valida -> scartata
        ["10/06/2026", None, "abc", "y"],      # importo non valido -> scartata
        ["10/06/2026", None, "-5,00", "ok"],   # valida
    ]
    res = econ_import.parse(rows)
    assert len(res["movimenti"]) == 1
    assert res["scartate"] == 2


# ---- import_transazioni (DB, dedup) ----

async def test_import_transazioni_inserisce_e_dedup(db):
    res = econ_import.parse(_PP_ROWS)
    movs = res["movimenti"]
    out1 = await db.import_transazioni("postepay_andrea", movs)
    assert out1["inserite"] == 3
    assert out1["duplicate"] == 0
    # reimport stesso file -> tutto duplicato
    out2 = await db.import_transazioni("postepay_andrea", movs)
    assert out2["inserite"] == 0
    assert out2["duplicate"] == 3
    # saldo coerente
    saldo = await db.get_saldo("postepay_andrea")
    assert saldo == round(-12.50 - 9.99 + 50.0, 2)


async def test_import_transazioni_conto_sconosciuto(db):
    import pytest
    with pytest.raises(ValueError):
        await db.import_transazioni("conto_inesistente", [])


async def test_import_registra_categorie(db):
    res = econ_import.parse(_BP_ROWS)
    await db.import_transazioni("bancoposta", res["movimenti"])
    cats = await db.list_categorie()
    assert "prelievo contanti" in cats


# TASK 4: due righe identiche nello stesso file -> hash diversi, non si perdono
_PP_DUP = [
    ["Data Contabile", "Data Valuta", "Importo (euro)", "Descrizione operazioni"],
    ["10/06/2026", "10/06/2026", "-1,00", "CAFFE BAR"],
    ["10/06/2026", "10/06/2026", "-1,00", "CAFFE BAR"],
]


def test_parse_righe_identiche_hash_diversi():
    res = econ_import.parse(_PP_DUP)
    assert len(res["movimenti"]) == 2
    h0, h1 = res["movimenti"][0]["hash"], res["movimenti"][1]["hash"]
    assert h0 != h1
    # la prima occorrenza usa l'hash storico (retrocompat)
    assert h0 == econ_import.row_hash("postepay", "2026-06-10", -1.0, "CAFFE BAR")


async def test_import_righe_identiche_e_reimport(db):
    movs = econ_import.parse(_PP_DUP)["movimenti"]
    out1 = await db.import_transazioni("postepay_andrea", movs)
    assert out1["inserite"] == 2 and out1["duplicate"] == 0
    out2 = await db.import_transazioni("postepay_andrea", movs)
    assert out2["inserite"] == 0 and out2["duplicate"] == 2
