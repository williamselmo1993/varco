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

print("OK")
