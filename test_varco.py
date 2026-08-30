"""Check end-to-end su ERP finto: permessi, flusso di approvazione, audit."""
import json
import os
import tempfile

os.environ["VARCO_DB"] = os.path.join(tempfile.mkdtemp(), "varco_test.db")
import varco_core as core

# lettura permessa
fatture = json.loads(core.erp_read("fatture"))
assert any(f["numero"] == "FT-2026-0141" for f in fatture)
core.check_read("sollecitatore", "fatture")

# lettura negata: sollecitatore non vede gli ordini
try:
    core.check_read("sollecitatore", "ordini")
    raise AssertionError("doveva negare la lettura")
except PermissionError:
    pass

# scrittura negata: sollecitatore non scrive da nessuna parte
try:
    core.request_write("sollecitatore", "create", "fatture", None, {"x": 1})
    raise AssertionError("doveva negare la scrittura")
except PermissionError:
    pass

# flusso completo: richiesta -> pending -> approvazione -> eseguita su ERP
rid = core.request_write("assistente-vendite", "create", "ordini", None,
                         {"numero": "OD-2026-0301", "cliente": "Rossi Costruzioni Srl",
                          "totale": 1500.0, "stato": "bozza"})
with core.db() as c:
    assert c.execute("SELECT status FROM approvals WHERE id=?", (rid,)).fetchone()[0] == "pending"
result = json.loads(core.decide(rid, approve=True))
assert result["esito"] == "creato"
ordini = json.loads(core.erp_read("ordini"))
assert any(o["numero"] == "OD-2026-0301" for o in ordini)

# rifiuto: l'ERP non viene toccato
rid2 = core.request_write("assistente-vendite", "update", "ordini", 1, {"stato": "annullato"})
assert core.decide(rid2, approve=False) == "rifiutata"
assert json.loads(core.erp_read("ordini", record_id=1))["stato"] == "confermato"

# doppia decisione bloccata
try:
    core.decide(rid2, approve=True)
    raise AssertionError("doveva bloccare la doppia decisione")
except ValueError:
    pass

# soglia di autonomia: sotto 1000 su ordini esegue subito, tracciato
rid3 = core.request_write("assistente-vendite", "create", "ordini", None,
                          {"numero": "OD-2026-0399", "cliente": "Verdi Impianti Snc",
                           "totale": 800.0})
with core.db() as c:
    assert c.execute("SELECT status FROM approvals WHERE id=?",
                     (rid3,)).fetchone()[0] == "auto-approvata"
assert any(o["numero"] == "OD-2026-0399" for o in json.loads(core.erp_read("ordini")))

# senza campo importo leggibile si chiede sempre (conservativo)
rid4 = core.request_write("assistente-vendite", "update", "clienti", 1,
                          {"email": "nuova@rossicostruzioni.it"})
with core.db() as c:
    assert c.execute("SELECT status FROM approvals WHERE id=?",
                     (rid4,)).fetchone()[0] == "pending"

# review-and-edit: il payload modificato e' quello eseguito
core.update_payload(rid4, {"email": "corretta@rossicostruzioni.it"})
core.decide(rid4, approve=True, note=" con modifiche dell'approvatore")
assert json.loads(core.erp_read("clienti", record_id=1))["email"] == "corretta@rossicostruzioni.it"

# audit: c'e' traccia sia dei negati sia delle approvazioni
with core.db() as c:
    stati = {r[0] for r in c.execute("SELECT DISTINCT status FROM audit").fetchall()}
assert "negato" in stati and "ok" in stati

print("OK")
