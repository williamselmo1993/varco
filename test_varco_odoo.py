"""Check dell'adapter Odoo (JSON-RPC) contro un finto server locale, senza Odoo vero."""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8578
CALLS = []


class FakeOdoo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append(body)
        p = body["params"]
        if p["service"] == "common" and p["method"] == "authenticate":
            result = 7
        else:  # execute_kw: args = [db, uid, password, model, method, args, kwargs]
            method = p["args"][4]
            result = {"search_read": [{"id": 1, "name": "SO001", "amount_total": 500.0}],
                      "read": [{"id": 5, "name": "Rossi Srl"}],
                      "create": 43,
                      "write": True}[method]
        out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *a):
        pass


tmp = tempfile.mkdtemp()
cfg = {
    "erp": {"tipo": "odoo", "base_url": f"http://127.0.0.1:{PORT}",
            "db": "testdb", "username": "api@test.it"},
    "entities": {
        "ordini": {"model": "sale.order", "campi": ["name", "amount_total"],
                   "campo_importo": "amount_total"},
        "clienti": {"model": "res.partner"},
    },
    "agents": {},
}
cfg_path = os.path.join(tmp, "cfg.json")
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f)
os.environ["VARCO_CONFIG"] = cfg_path
os.environ["VARCO_DB"] = os.path.join(tmp, "varco_test.db")
os.environ["ERP_API_TOKEN"] = "chiave-api-test"

srv = HTTPServer(("127.0.0.1", PORT), FakeOdoo)
threading.Thread(target=srv.serve_forever, daemon=True).start()

import varco_core as core

# search: authenticate + search_read con domain, fields e limit corretti
out = json.loads(core.erp_read("ordini", params={"state": "sale"}, limit=5))
assert out[0]["name"] == "SO001"
auth = CALLS[0]["params"]
assert auth == {"service": "common", "method": "authenticate",
                "args": ["testdb", "api@test.it", "chiave-api-test", {}]}
sr = CALLS[1]["params"]["args"]
assert sr[:5] == ["testdb", 7, "chiave-api-test", "sale.order", "search_read"]
assert sr[5] == [[["state", "=", "sale"]]]
assert sr[6] == {"limit": 5, "fields": ["name", "amount_total"]}

# get per id -> read
assert json.loads(core.erp_read("clienti", record_id=5))["name"] == "Rossi Srl"
rd = CALLS[2]["params"]["args"]
assert rd[3:6] == ["res.partner", "read", [[5]]]

# create e write
assert json.loads(core.erp_execute("create", "ordini", None,
                                   {"partner_id": 5, "amount_total": 900.0}))["id"] == 43
cr = CALLS[3]["params"]["args"]
assert cr[3:6] == ["sale.order", "create", [{"partner_id": 5, "amount_total": 900.0}]]

assert json.loads(core.erp_execute("update", "ordini", "43",
                                   {"state": "sale"}))["esito"] == "aggiornato"
wr = CALLS[4]["params"]["args"]
assert wr[3:6] == ["sale.order", "write", [[43], {"state": "sale"}]]

srv.shutdown()
print("OK")
