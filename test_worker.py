"""Check del worker: inbox -> modello (mockato) -> richiesta Varco; benchmark dei modelli."""
import json
import os
import tempfile

tmp = tempfile.mkdtemp()
cfg = json.load(open("varco_config.json", encoding="utf-8-sig"))
cfg["workflows"] = {"ordini-da-email": {
    "agent": "assistente-vendite", "entity": "ordini",
    "inbox": os.path.join(tmp, "inbox"), "campi": ["numero", "cliente", "totale", "note"]}}
cfg_path = os.path.join(tmp, "cfg.json")
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f)
os.environ["VARCO_CONFIG"] = cfg_path
os.environ["VARCO_DB"] = os.path.join(tmp, "varco_test.db")

import varco_core as core
import varco_worker as w

inbox = os.path.join(tmp, "inbox")
os.makedirs(inbox)
with open(os.path.join(inbox, "ordine1.txt"), "w", encoding="utf-8") as f:
    f.write("Ordine come da offerta 118, totale 2.480,00 euro. Rossi Costruzioni Srl")

# modello mockato: risposta con fence markdown e importo in formato italiano
FENCE = "`" * 3
w.call_model = lambda *a, **k: (
    FENCE + 'json\n{"numero": "OD-EM-1", "cliente": "Rossi Costruzioni Srl", '
    '"totale": "2.480,00", "note": "40 sacchi cemento"}\n' + FENCE)
rids = w.process_inbox()
assert len(rids) == 1
with core.db() as c:
    row = c.execute("SELECT status, payload, modello FROM approvals WHERE id=?",
                    (rids[0],)).fetchone()
p = json.loads(row["payload"])
assert p["cliente"] == "Rossi Costruzioni Srl" and p["totale"] == 2480.0
assert p["origine"] == "ordine1.txt"
assert row["status"] == "pending" and row["modello"] == w.MODEL   # 2480 > soglia 1000

assert w.process_inbox() == []                                     # niente doppioni

# risposta inutilizzabile: errore tracciato, nessuna richiesta, file non riprocessato
with open(os.path.join(inbox, "ordine2.txt"), "w", encoding="utf-8") as f:
    f.write("boh")
w.call_model = lambda *a, **k: "non ho capito il documento"
assert w.process_inbox() == []
with core.db() as c:
    err = c.execute("SELECT COUNT(*) FROM audit WHERE action='estrazione' "
                    "AND status='errore'").fetchone()[0]
assert err == 1

# benchmark: la correzione dell'approvatore conta come errore del modello
core.update_payload(rids[0], dict(p, totale=2400.0))
core.decide(rids[0], True, note=" con modifiche dell'approvatore")
b = core.benchmark_modelli()
assert b[0]["modello"] == w.MODEL and b[0]["corrette"] == 1 and b[0]["tasso_errore"] == 1.0

# numeri: formato italiano e inglese
assert w._numero("1.250,50") == 1250.5 and w._numero("1250.5") == 1250.5
assert w._numero(7) == 7.0

print("OK")
