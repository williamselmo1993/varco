"""Varco — control plane tra agenti AI ed ERP: permessi, approvazione umana, audit.

Stato su SQLite (varco.db), condiviso tra il server MCP e la dashboard.
ERP reale via REST (varco_config.json) oppure finto ("base_url": "mock")
con dati italiani di esempio per demo e test.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
CONFIG = json.loads(
    Path(os.environ.get("VARCO_CONFIG", BASE_DIR / "varco_config.json")).read_text(encoding="utf-8"))
DB_PATH = Path(os.environ.get("VARCO_DB", BASE_DIR / "varco.db"))
ERP = CONFIG["erp"]
ENTITIES = CONFIG["entities"]
AGENTS = CONFIG["agents"]
DEPARTMENTS = CONFIG.get("departments", {})
TIMEOUT = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, action TEXT,
  entity TEXT, record_id TEXT, detail TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent TEXT, action TEXT,
  entity TEXT, record_id TEXT, payload TEXT, status TEXT DEFAULT 'pending',
  decided_ts TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS mock_records (
  entity TEXT, id INTEGER, data TEXT, PRIMARY KEY (entity, id));
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:  # migrazione: flag notifica Telegram sui DB esistenti
        conn.execute("ALTER TABLE approvals ADD COLUMN notified INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    return conn


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def audit(agent: str, action: str, entity: str = "", record_id: str = "",
          detail: str = "", status: str = "ok") -> None:
    with db() as c:
        c.execute(
            "INSERT INTO audit (ts, agent, action, entity, record_id, detail, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (now(), agent, action, entity, str(record_id), detail[:500], status))


# ---------------------------------------------------------------- permessi

def agent_perms(agent: str) -> dict:
    if agent not in AGENTS:
        raise PermissionError(f"Agente '{agent}' non registrato in Varco")
    return AGENTS[agent]


def _check_entity(entity: str) -> None:
    if entity not in ENTITIES:
        raise ValueError(
            f"Entita' sconosciuta '{entity}'. Disponibili: {', '.join(sorted(ENTITIES))}")


def check_read(agent: str, entity: str) -> None:
    _check_entity(entity)
    if entity not in agent_perms(agent).get("read", []):
        audit(agent, "read", entity, status="negato")
        raise PermissionError(f"L'agente '{agent}' non ha permesso di lettura su '{entity}'")


def check_write(agent: str, entity: str) -> None:
    _check_entity(entity)
    if entity not in agent_perms(agent).get("write", []):
        audit(agent, "write", entity, status="negato")
        raise PermissionError(f"L'agente '{agent}' non ha permesso di scrittura su '{entity}'")


# ---------------------------------------------------------------- ERP (reale o mock)

SEED = {
    "clienti": [
        {"ragione_sociale": "Rossi Costruzioni Srl", "piva": "01234567890",
         "email": "amministrazione@rossicostruzioni.it"},
        {"ragione_sociale": "Bianchi Alimentari Spa", "piva": "09876543210",
         "email": "fornitori@bianchialimentari.it"},
        {"ragione_sociale": "Verdi Impianti Snc", "piva": "05555444433",
         "email": "info@verdimpianti.it"},
    ],
    "fatture": [
        {"numero": "FT-2026-0141", "cliente": "Rossi Costruzioni Srl",
         "importo": 12400.00, "scadenza": "2026-07-31", "stato": "scaduta"},
        {"numero": "FT-2026-0158", "cliente": "Bianchi Alimentari Spa",
         "importo": 8300.50, "scadenza": "2026-09-15", "stato": "aperta"},
        {"numero": "FT-2026-0163", "cliente": "Verdi Impianti Snc",
         "importo": 2150.00, "scadenza": "2026-08-10", "stato": "scaduta"},
    ],
    "ordini": [
        {"numero": "OD-2026-0287", "cliente": "Bianchi Alimentari Spa",
         "totale": 4300.00, "stato": "confermato"},
    ],
    "fornitori": [
        {"ragione_sociale": "Ferramenta Lombarda Srl", "piva": "02233445566",
         "email": "ordini@ferramentalombarda.it"},
        {"ragione_sociale": "Chimica Adriatica Spa", "piva": "07788990011",
         "email": "vendite@chimicaadriatica.it"},
    ],
    "articoli": [
        {"codice": "ART-0101", "descrizione": "Shampoo professionale 5L",
         "giacenza": 42, "scorta_minima": 20},
        {"codice": "ART-0102", "descrizione": "Guanti nitrile M (conf. 100)",
         "giacenza": 8, "scorta_minima": 25},
        {"codice": "ART-0103", "descrizione": "Disinfettante superfici 1L",
         "giacenza": 30, "scorta_minima": 15},
    ],
    "ordini_acquisto": [
        {"numero": "OA-2026-0114", "fornitore": "Ferramenta Lombarda Srl",
         "totale": 960.00, "stato": "inviato"},
    ],
    "preventivi": [
        {"numero": "PR-2026-0045", "cliente": "Bianchi Alimentari Spa",
         "totale": 3200.00, "stato": "inviato", "scadenza": "2026-09-10"},
    ],
    "ddt_ingresso": [
        {"numero": "DDT-1187", "fornitore": "Ferramenta Lombarda Srl",
         "colli": 12, "riferimento_ordine": "OA-2026-0114", "stato": "da registrare"},
    ],
}


def _mock() -> bool:
    return ERP["base_url"] == "mock"


def seed_mock() -> None:
    with db() as c:
        for entity, rows in SEED.items():  # per-entita': i DB esistenti ricevono le entita' nuove
            if c.execute("SELECT COUNT(*) FROM mock_records WHERE entity=?",
                         (entity,)).fetchone()[0]:
                continue
            for i, r in enumerate(rows, 1):
                c.execute("INSERT INTO mock_records VALUES (?,?,?)",
                          (entity, i, json.dumps(r, ensure_ascii=False)))


def _token() -> str:
    token = os.environ.get("ERP_API_TOKEN", "")
    if not token:
        raise RuntimeError("Variabile d'ambiente ERP_API_TOKEN mancante")
    return token


def _headers() -> dict:
    return {ERP.get("auth_header", "Authorization"):
            ERP.get("auth_format", "Bearer {token}").format(token=_token())}


# --- adapter Odoo (JSON-RPC): "tipo": "odoo" nel config, API key in ERP_API_TOKEN

_ODOO_UID = None


def _odoo_call(service: str, method: str, args: list):
    payload = {"jsonrpc": "2.0", "method": "call", "id": 1,
               "params": {"service": service, "method": method, "args": args}}
    r = httpx.post(ERP["base_url"].rstrip("/") + "/jsonrpc", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    out = r.json()
    if out.get("error"):
        err = out["error"]
        raise RuntimeError("Odoo: " + str(err.get("data", {}).get("message")
                                          or err.get("message") or err))
    return out.get("result")


def _odoo_uid() -> int:
    global _ODOO_UID
    if _ODOO_UID is None:
        _ODOO_UID = _odoo_call("common", "authenticate",
                               [ERP["db"], ERP["username"], _token(), {}])
        if not _ODOO_UID:
            raise RuntimeError("Autenticazione Odoo fallita: controlla db, username e ERP_API_TOKEN")
    return _ODOO_UID


def _odoo_exec(model: str, method: str, args: list, kwargs: dict | None = None):
    return _odoo_call("object", "execute_kw",
                      [ERP["db"], _odoo_uid(), _token(), model, method, args, kwargs or {}])


def erp_read(entity: str, record_id=None, params: dict | None = None, limit: int = 20) -> str:
    if ERP.get("tipo") == "odoo":
        model = ENTITIES[entity]["model"]
        campi = ENTITIES[entity].get("campi") or []
        if record_id is not None:
            recs = _odoo_exec(model, "read", [[int(record_id)]],
                              {"fields": campi} if campi else {})
            if not recs:
                raise ValueError(f"Record {record_id} non trovato in '{entity}'")
            return json.dumps(recs[0], ensure_ascii=False, default=str)
        domain = [[k, "=", v] for k, v in (params or {}).items()]
        kwargs = {"limit": limit}
        if campi:
            kwargs["fields"] = campi
        recs = _odoo_exec(model, "search_read", [domain], kwargs)
        return json.dumps(recs, ensure_ascii=False, default=str)
    if _mock():
        seed_mock()
        with db() as c:
            if record_id is not None:
                row = c.execute("SELECT data FROM mock_records WHERE entity=? AND id=?",
                                (entity, int(record_id))).fetchone()
                if not row:
                    raise ValueError(f"Record {record_id} non trovato in '{entity}'")
                return json.dumps({"id": int(record_id), **json.loads(row["data"])},
                                  ensure_ascii=False)
            rows = c.execute("SELECT id, data FROM mock_records WHERE entity=?",
                             (entity,)).fetchall()
        out = [{"id": r["id"], **json.loads(r["data"])} for r in rows]
        if params:  # ponytail: filtro esatto chiave=valore; query ricche quando serviranno
            out = [r for r in out
                   if all(str(r.get(k)) == str(v) for k, v in params.items())]
        return json.dumps(out[:limit], ensure_ascii=False)

    url = ERP["base_url"].rstrip("/") + ENTITIES[entity]["path"] + (
        f"/{record_id}" if record_id is not None else "")
    q = dict(params or {})
    if ERP.get("limit_param") and record_id is None:
        q.setdefault(ERP["limit_param"], limit)
    r = httpx.get(url, headers=_headers(), params=q, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def erp_execute(action: str, entity: str, record_id, payload) -> str:
    """Esegue una scrittura GIA' approvata. Mai chiamare direttamente dai tool MCP."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    if ERP.get("tipo") == "odoo":
        model = ENTITIES[entity]["model"]
        if action == "create":
            new_id = _odoo_exec(model, "create", [data])
            return json.dumps({"id": new_id, "esito": "creato"})
        ok = _odoo_exec(model, "write", [[int(record_id)], data])
        return json.dumps({"id": int(record_id),
                           "esito": "aggiornato" if ok else "errore"})
    if _mock():
        seed_mock()
        with db() as c:
            if action == "create":
                new_id = (c.execute(
                    "SELECT COALESCE(MAX(id),0) FROM mock_records WHERE entity=?",
                    (entity,)).fetchone()[0]) + 1
                c.execute("INSERT INTO mock_records VALUES (?,?,?)",
                          (entity, new_id, json.dumps(data, ensure_ascii=False)))
                return json.dumps({"id": new_id, "esito": "creato"})
            row = c.execute("SELECT data FROM mock_records WHERE entity=? AND id=?",
                            (entity, int(record_id))).fetchone()
            if not row:
                raise ValueError(f"Record {record_id} non trovato in '{entity}'")
            merged = {**json.loads(row["data"]), **data}
            c.execute("UPDATE mock_records SET data=? WHERE entity=? AND id=?",
                      (json.dumps(merged, ensure_ascii=False), entity, int(record_id)))
            return json.dumps({"id": int(record_id), "esito": "aggiornato"})

    url = ERP["base_url"].rstrip("/") + ENTITIES[entity]["path"] + (
        f"/{record_id}" if action == "update" else "")
    method = "POST" if action == "create" else "PUT"
    r = httpx.request(method, url, headers=_headers(), json=data, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


# ---------------------------------------------------------------- policy e approvazioni

def policy_auto_approve(agent: str, entity: str, data: dict) -> bool:
    """True se la scrittura rientra nella soglia di autonomia dell'agente.

    Conservativo, stile Ramp: se manca la soglia, il campo importo o il valore
    non è leggibile, si chiede sempre l'approvazione umana.
    """
    soglia = AGENTS[agent].get("soglie", {}).get(entity)
    campo = ENTITIES[entity].get("campo_importo")
    if soglia is None or not campo or campo not in data:
        return False
    try:
        importo = float(data[campo])
    except (TypeError, ValueError):
        return False
    return 0 <= importo <= float(soglia)


def request_write(agent: str, action: str, entity: str, record_id, data: dict) -> int:
    check_write(agent, entity)
    with db() as c:
        cur = c.execute(
            "INSERT INTO approvals (ts, agent, action, entity, record_id, payload) "
            "VALUES (?,?,?,?,?,?)",
            (now(), agent, action, entity, str(record_id or ""),
             json.dumps(data, ensure_ascii=False)))
        rid = cur.lastrowid
    if not policy_auto_approve(agent, entity, data):
        audit(agent, f"richiesta-{action}", entity, record_id or "",
              json.dumps(data, ensure_ascii=False), "in-attesa")
        return rid
    try:
        result = erp_execute(action, entity, record_id or None, data)
        _close(rid, "auto-approvata", result)
        audit(agent, "auto-approvazione", entity, record_id or "",
              f"richiesta #{rid} entro soglia di autonomia, eseguita")
    except Exception as e:
        _close(rid, "errore", str(e))
        audit(agent, "auto-approvazione", entity, record_id or "",
              f"richiesta #{rid}: {e}", "errore")
    return rid


def update_payload(approval_id: int, data: dict) -> None:
    """Sostituisce i dati di una richiesta ancora pending (review-and-edit)."""
    with db() as c:
        cur = c.execute("UPDATE approvals SET payload=? WHERE id=? AND status='pending'",
                        (json.dumps(data, ensure_ascii=False), approval_id))
        if cur.rowcount == 0:
            raise ValueError(f"Richiesta {approval_id} inesistente o gia' decisa")


def decide(approval_id: int, approve: bool, note: str = "", decided_by: str = "umano") -> str:
    with db() as c:
        row = c.execute("SELECT * FROM approvals WHERE id=? AND status='pending'",
                        (approval_id,)).fetchone()
    if not row:
        raise ValueError(f"Richiesta {approval_id} inesistente o gia' decisa")
    if not approve:
        _close(approval_id, "rifiutata", "")
        audit(decided_by, "rifiuto", row["entity"], row["record_id"],
              f"richiesta #{approval_id}{note}")
        return "rifiutata"
    try:
        with db() as c:  # ricarica: il payload puo' essere stato modificato dall'approvatore
            row = c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        result = erp_execute(row["action"], row["entity"], row["record_id"] or None,
                             row["payload"])
        _close(approval_id, "approvata", result)
        audit(decided_by, "approvazione", row["entity"], row["record_id"],
              f"richiesta #{approval_id} eseguita su ERP{note}")
        return result
    except Exception as e:
        _close(approval_id, "errore", str(e))
        audit(decided_by, "approvazione", row["entity"], row["record_id"],
              f"richiesta #{approval_id}: {e}", "errore")
        raise


def _close(approval_id: int, status: str, result: str) -> None:
    with db() as c:
        c.execute("UPDATE approvals SET status=?, decided_ts=?, result=? WHERE id=?",
                  (status, now(), str(result)[:1000], approval_id))
