"""Check del ramo ERP REALE (REST) di varco_core: URL, header di auth, query, body.

Avvia un finto ERP REST in-process (http.server su 127.0.0.1:8577) che registra
ogni richiesta ricevuta, poi punta varco_core su di esso tramite un config
temporaneo (VARCO_CONFIG) impostato PRIMA dell'import.
"""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HOST, PORT = "127.0.0.1", 8577
TMP = tempfile.mkdtemp()

# --- env PRIMA dell'import: db temporaneo, config temporaneo, token finto ---
os.environ["VARCO_DB"] = os.path.join(TMP, "varco_rest_test.db")
config = {
    "erp": {
        "base_url": f"http://{HOST}:{PORT}",
        "auth_header": "Authorization",
        "auth_format": "token {token}",
        "limit_param": "limit_page_length",
    },
    "entities": {
        "clienti": {"path": "/Customer", "description": "Anagrafica clienti"},
        "ordini": {"path": "/Sales Order", "description": "Ordini di vendita"},
        "fatture": {"path": "/Sales Invoice", "description": "Fatture di vendita"},
    },
    "agents": {
        "assistente-vendite": {"read": ["clienti", "ordini", "fatture"],
                               "write": ["ordini"]},
        "sollecitatore": {"read": ["clienti", "fatture"], "write": []},
    },
}
config_path = os.path.join(TMP, "varco_config_rest.json")
with open(config_path, "w", encoding="utf-8") as f:
    json.dump(config, f)
os.environ["VARCO_CONFIG"] = config_path
os.environ["ERP_API_TOKEN"] = "test-token"

# --- finto ERP REST: registra le richieste e risponde JSON ---
received = []


class FakeERP(BaseHTTPRequestHandler):
    def _handle(self):
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        received.append({
            "method": self.command,
            "path": unquote(parts.path),
            "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
            "headers": dict(self.headers),
            "body": json.loads(body) if body else None,
        })
        out = json.dumps({"data": [{"name": "CUST-0001"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *args):  # silenzioso
        pass


server = ThreadingHTTPServer((HOST, PORT), FakeERP)
threading.Thread(target=server.serve_forever, daemon=True).start()

import varco_core as core

assert not core._mock(), "il config temporaneo deve attivare il ramo REST"

# (a) erp_read lista: GET /Customer con auth header e limit param in query
core.erp_read("clienti")
req = received[-1]
assert req["method"] == "GET", req
assert req["path"] == "/Customer", req
assert req["headers"].get("Authorization") == "token test-token", req["headers"]
assert req["query"].get("limit_page_length") == "20", req["query"]

# (b) erp_read singolo record: GET /Customer/7, senza limit param
core.erp_read("clienti", record_id=7)
req = received[-1]
assert req["method"] == "GET", req
assert req["path"] == "/Customer/7", req
assert req["headers"].get("Authorization") == "token test-token", req["headers"]
assert "limit_page_length" not in req["query"], req["query"]

# (c) create: POST /Sales Order con il body JSON
payload = {"numero": "OD-2026-0400", "cliente": "Rossi Costruzioni Srl", "totale": 990.0}
core.erp_execute("create", "ordini", None, payload)
req = received[-1]
assert req["method"] == "POST", req
assert req["path"] == "/Sales Order", req
assert req["headers"].get("Authorization") == "token test-token", req["headers"]
assert req["body"] == payload, req["body"]

# (d) update: PUT /Sales Order/7 con il body JSON
core.erp_execute("update", "ordini", "7", {"stato": "annullato"})
req = received[-1]
assert req["method"] == "PUT", req
assert req["path"] == "/Sales Order/7", req
assert req["body"] == {"stato": "annullato"}, req["body"]

# (e) senza ERP_API_TOKEN deve alzare RuntimeError
del os.environ["ERP_API_TOKEN"]
try:
    core.erp_read("clienti")
    raise AssertionError("doveva alzare RuntimeError senza ERP_API_TOKEN")
except RuntimeError:
    pass
finally:
    os.environ["ERP_API_TOKEN"] = "test-token"

server.shutdown()
print("OK")
