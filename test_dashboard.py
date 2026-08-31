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
assert "Approva tutte le ordinarie (2)" in c.get("/").text
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

# app shell: stato in data-s e /ping coerente (il client ricarica solo se cambia)
pagina = c2.get("/").text
assert 'data-s="' in pagina and "fetch('/ping')" in pagina
assert c2.get("/ping").text == dash.app_state()

# nuovi workflow a catalogo
assert "Preventivi Rapidi" in c2.get("/reparto/vendite").text
assert "Ricevimento Merci" in c2.get("/reparto/magazzino").text
assert "PR-2026-0045" in c2.get("/dati/preventivi").text
assert "DDT-1187" in c2.get("/dati/ddt_ingresso").text
dash.APPROVERS = {}

# --- pattern YC/US -----------------------------------------------------------
c3 = TestClient(dash.app)

# due secchi + motivo + eta' + tastiera: uno urgente (>5000) e uno ordinario
ru = core.request_write("assistente-vendite", "create", "ordini", None,
                        {"numero": "OD-URG", "cliente": "Rossi Costruzioni Srl",
                         "totale": 9000.0})
ro = core.request_write("assistente-vendite", "create", "ordini", None,
                        {"numero": "OD-ORD", "cliente": "Verdi Impianti Snc",
                         "totale": 1800.0})
h = c3.get("/").text
assert "Da guardare bene (1)" in h and "Entro le tue policy" in h
assert "supera € 5.000" in h and "supera la sua soglia di autonomia" in h  # motivi Ramp-style
assert "in attesa da" in h and 'data-rid=' in h and "kbd-help" in h        # eta' + tastiera
assert "Chiedi una modifica" in h and "Ricorda: approva da solo" in h

# il bulk non tocca mai le urgenti
c3.post("/decide_all", follow_redirects=True)
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (ru,)).fetchone()[0] == "pending"
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (ro,)).fetchone()[0] == "approvata"

# chiedi una modifica: la richiesta torna all'assistente con la nota
c3.post("/decide", data={"id": str(ru), "azione": "chiarimenti",
                         "domanda": "usa il listino 2026"}, follow_redirects=True)
with core.db() as db_:
    row = db_.execute("SELECT status, result FROM approvals WHERE id=?",
                      (ru,)).fetchone()
assert row[0] == "chiarimenti" and "listino 2026" in row[1]
assert "una modifica su Ordini di vendita" in c3.get("/attivita").text

# regola "ricorda": approva con memoria -> la prossima identica passa da sola
rr = core.request_write("assistente-vendite", "create", "ordini", None,
                        {"numero": "OD-R1", "cliente": "Bianchi Alimentari Spa",
                         "totale": 4000.0})
c3.post("/decide", data={"id": str(rr), "azione": "approva", "ricorda": "on",
                         "f_numero": "OD-R1", "f_cliente": "Bianchi Alimentari Spa",
                         "f_totale": "4000"}, follow_redirects=True)
rr2 = core.request_write("assistente-vendite", "create", "ordini", None,
                         {"numero": "OD-R2", "cliente": "Bianchi Alimentari Spa",
                          "totale": 4100.0})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rr2,)).fetchone()[0] == "auto-approvata"
pag = c3.get("/agente/assistente-vendite").text
assert "Regole di autonomia" in pag and "Bianchi Alimentari Spa" in pag

# revoca: la successiva identica torna a chiedere
with core.db() as db_:
    rule_id = db_.execute("SELECT id FROM auto_rules LIMIT 1").fetchone()[0]
c3.post("/regola", data={"id": str(rule_id)}, follow_redirects=True)
rr3 = core.request_write("assistente-vendite", "create", "ordini", None,
                         {"numero": "OD-R3", "cliente": "Bianchi Alimentari Spa",
                          "totale": 4200.0})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rr3,)).fetchone()[0] == "pending"

# --- multi-user: doppia firma, limiti, ruoli, puo'-decidere -----------------
os.environ["VARCO_APPROVERS"] = "w:k1:*:5000:admin;v:k2:vendite::viewer"
p = dash._parse_approvers()
assert p["w"]["limit"] == 5000 and p["w"]["role"] == "admin"
assert p["v"]["role"] == "viewer" and p["v"]["limit"] is None
del os.environ["VARCO_APPROVERS"]

c4 = TestClient(dash.app)
dash.APPROVERS = {
    "anna": {"key": "kA", "depts": None, "limit": 2000.0, "role": "approver"},
    "will": {"key": "kW", "depts": None, "limit": None, "role": "approver"},
    "boss": {"key": "kB", "depts": None, "limit": None, "role": "approver"},
    "revisore": {"key": "kR", "depts": None, "limit": None, "role": "viewer"},
}
rid4e = core.request_write("assistente-vendite", "create", "ordini", None,
                          {"numero": "OD-UI4", "cliente": "Verdi Impianti Snc",
                           "totale": 15000.0})
c4.post("/login", data={"chiave": "kW"})
h4 = c4.get("/").text
assert "4 occhi" in h4 and "Può decidere:" in h4
assert "Approvazione consigliata" in h4              # badge sul pending ordinario

# anna (limite 2000) non puo' decidere i 15.000: non compare e il POST e' bloccato
assert "anna" not in h4.split("Può decidere:")[1].split("</div>")[0]
c4.post("/login", data={"chiave": "kA"})
c4.post("/decide", data={"id": str(rid4e), "azione": "approva"})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rid4e,)).fetchone()[0] == "pending"

# viewer: sola visualizzazione, POST bloccato
c4.post("/login", data={"chiave": "kR"})
assert "sola visualizzazione" in c4.get("/").text
c4.post("/decide", data={"id": str(rid4e), "azione": "approva"})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rid4e,)).fetchone()[0] == "pending"

# 4 occhi via web: will firma (resta pending), boss chiude
c4.post("/login", data={"chiave": "kW"})
c4.post("/decide", data={"id": str(rid4e), "azione": "approva"})
h4b = c4.get("/").text
assert "Prima firma: will" in h4b and "la prima firma" in c4.get("/attivita").text
c4.post("/login", data={"chiave": "kB"})
c4.post("/decide", data={"id": str(rid4e), "azione": "approva"})
with core.db() as db_:
    assert db_.execute("SELECT status FROM approvals WHERE id=?",
                       (rid4e,)).fetchone()[0] == "approvata"
dash.APPROVERS = {}

print("OK")
