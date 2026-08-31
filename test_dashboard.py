"""Check dell'app: navigazione sidebar, pagine, frasi umane, decisione."""
import os
import tempfile

os.environ["VARCO_DB"] = os.path.join(tempfile.mkdtemp(), "varco_test.db")
import varco_core as core
import varco_dashboard as dash
from starlette.testclient import TestClient

core.audit("sollecitatore", "search", "fatture")
rid = core.request_write("assistente-acquisti", "create", "ordini_acquisto",
                         None, {"numero": "OA-2026-0115", "totale": 480.0})

c = TestClient(dash.app)

home = c.get("/").text
assert "Panoramica" in home and "Da approvare" in home
assert "vuole creare" in home and "ordine d&#x27;acquisto" in home
assert "In attesa di te" in home                       # processo a passi
assert "/reparto/vendite" in home and "/agente/sollecitatore" in home  # menu multilivello
assert "/dati/articoli" in home                        # sezione Gestionale nel menu

rep = c.get("/reparto/acquisti").text
assert "Assistente Acquisti" in rep and "sotto scorta minima" in rep

ag = c.get("/agente/sollecitatore").text
assert "Recupero Crediti" in ag and "Solo consultazione" in ag
assert "ha consultato Fatture" in ag                   # feed filtrato per agente

art = c.get("/dati/articoli").text
assert "Guanti nitrile" in art and "Sotto scorta" in art

att = c.get("/attivita").text
assert "ha consultato" in att

assert c.get("/reparto/inesistente", follow_redirects=False).status_code == 303

assert "Autonomia: esegue da solo" in rep          # soglia visibile sulla scheda agente

r = c.post("/decide", data={"id": str(rid), "azione": "rifiuta"}, follow_redirects=True)
assert "Rifiutata da te" in r.text and "Gestionale non toccato" in r.text

# review-and-edit: correggo il totale prima di approvare (5000 > soglia -> pending)
rid2 = core.request_write("assistente-vendite", "create", "ordini", None,
                          {"numero": "OD-EDIT", "cliente": "Rossi Costruzioni Srl",
                           "totale": 5000.0})
c.post("/decide", data={"id": str(rid2), "azione": "approva", "f_numero": "OD-EDIT",
                        "f_cliente": "Rossi Costruzioni Srl", "f_totale": "4500"},
       follow_redirects=True)
import json
ordini = json.loads(core.erp_read("ordini"))
assert any(o.get("numero") == "OD-EDIT" and o.get("totale") == 4500 for o in ordini)

# auto-approvazione visibile nel feed
core.request_write("assistente-vendite", "create", "ordini", None,
                   {"numero": "OD-AUTO", "cliente": "Verdi Impianti Snc", "totale": 200.0})
assert "da solo — entro la sua soglia di autonomia" in c.get("/attivita").text

# telegram: testo e tastiera (funzioni pure, senza rete)
riga = {"id": 9, "agent": "assistente-vendite", "action": "create", "entity": "ordini",
        "payload": '{"numero": "OD-9", "totale": 120.0}'}
t = dash.tg_text(riga)
assert "Assistente Vendite" in t and "#9" in t and "Totale: 120.0" in t
kb = dash.tg_keyboard(9)
assert kb["inline_keyboard"][0][0]["callback_data"] == "a:9"

# soglia_web: sopra 5000 su ordini niente one-tap; digest: gate sull'orario
alta = dict(riga, payload='{"numero": "OD-BIG", "totale": 9000.0}')
assert dash.tg_urgent(alta) and not dash.tg_urgent(riga)
assert dash.digest_due("09:00", ["09:00", "14:00"]) and not dash.digest_due("09:01", ["09:00"])

# metriche ROI in home
home2 = c.get("/").text
assert "tempo risparmiato" in home2 and "in autonomia" in home2

# approvazione in blocco: due pending -> bottone -> tutte approvate
r1 = core.request_write("assistente-vendite", "create", "ordini", None,
                        {"numero": "OD-B1", "cliente": "Rossi Costruzioni Srl", "totale": 3000.0})
r2 = core.request_write("assistente-vendite", "create", "ordini", None,
                        {"numero": "OD-B2", "cliente": "Verdi Impianti Snc", "totale": 2000.0})
assert "Approva tutte (2)" in c.get("/").text
c.post("/decide_all", follow_redirects=True)
with core.db() as db_:
    stati = {row[0] for row in db_.execute(
        "SELECT status FROM approvals WHERE id IN (?,?)", (r1, r2)).fetchall()}
assert stati == {"approvata"}

# login: con VARCO_ACCESS_KEY le pagine sono protette
dash.ACCESS_KEY = "chiave-test"
assert c.get("/", follow_redirects=False).status_code == 303
assert "errata" in c.post("/login", data={"chiave": "sbagliata"}).text
ok = c.post("/login", data={"chiave": "chiave-test"}, follow_redirects=False)
assert ok.status_code == 303
assert c.get("/").status_code == 200  # il cookie di sessione ora vale

# approvatori con deleghe: anna solo amministrazione, will tutti i reparti
c2 = TestClient(dash.app)
dash.ACCESS_KEY = ""
dash.APPROVERS = {"anna": {"key": "kA", "depts": ["amministrazione"]},
                  "will": {"key": "kW", "depts": None}}
c2.post("/login", data={"chiave": "kA"})
rid3 = core.request_write("assistente-vendite", "create", "ordini", None,
                          {"numero": "OD-DEL", "cliente": "Rossi Costruzioni Srl",
                           "totale": 1500.0})
assert "Fuori dalla tua delega" in c2.get("/").text
c2.post("/decide", data={"id": str(rid3), "azione": "approva"})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rid3,)).fetchone()[0] == "pending"   # anna non puo'
c2.post("/login", data={"chiave": "kW"})
c2.post("/decide", data={"id": str(rid3), "azione": "approva"})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rid3,)).fetchone()[0] == "approvata"  # will si'
assert "will ha approvato" in c2.get("/attivita").text        # audit col nome

# export CSV dell'audit
csv_out = c2.get("/export/audit.csv")
assert csv_out.status_code == 200 and "quando;chi" in csv_out.text

# nuovi workflow a catalogo
assert "Preventivi Rapidi" in c2.get("/reparto/vendite").text
assert "Ricevimento Merci" in c2.get("/reparto/magazzino").text
assert "PR-2026-0045" in c2.get("/dati/preventivi").text
assert "DDT-1187" in c2.get("/dati/ddt_ingresso").text
dash.APPROVERS = {}

print("OK")
