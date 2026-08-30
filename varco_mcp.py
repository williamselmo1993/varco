"""Server MCP di Varco: la faccia verso l'agente AI.

Ogni agente si collega con la propria identita' (env VARCO_AGENT) e vede solo
cio' che i suoi permessi consentono. Le scritture non toccano mai l'ERP
direttamente: creano una richiesta che un umano approva sulla dashboard.
"""
import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

import varco_core as core

AGENT = os.environ.get("VARCO_AGENT", "")
mcp = MCPServer("varco")


@mcp.tool()
def list_entities() -> str:
    """Entita' ERP disponibili e permessi di questo agente."""
    perms = core.agent_perms(AGENT)
    return json.dumps(
        {e: {"descrizione": v.get("description", ""),
             "lettura": e in perms.get("read", []),
             "scrittura": e in perms.get("write", [])}
         for e, v in core.ENTITIES.items()},
        ensure_ascii=False, indent=2)


@mcp.tool()
def search(entity: str, filters: dict | None = None, limit: int = 20) -> str:
    """Cerca record di un'entita' (es. fatture con {"stato": "scaduta"})."""
    try:
        core.check_read(AGENT, entity)
        out = core.erp_read(entity, params=filters, limit=limit)
    except (PermissionError, ValueError) as e:
        raise ToolError(str(e)) from e  # errore atteso: il messaggio deve arrivare all'agente
    core.audit(AGENT, "search", entity, detail=json.dumps(filters or {}, ensure_ascii=False))
    return out


@mcp.tool()
def get(entity: str, record_id: str) -> str:
    """Legge un singolo record per ID."""
    try:
        core.check_read(AGENT, entity)
        out = core.erp_read(entity, record_id=record_id)
    except (PermissionError, ValueError) as e:
        raise ToolError(str(e)) from e
    core.audit(AGENT, "get", entity, record_id)
    return out


def _esito_richiesta(rid: int) -> str:
    with core.db() as c:
        row = c.execute("SELECT status, result FROM approvals WHERE id=?", (rid,)).fetchone()
    if row["status"] == "auto-approvata":
        return (f"Eseguita subito (richiesta #{rid}): entro la soglia di autonomia "
                f"dell'agente, tracciata nell'audit. Risultato: {row['result']}")
    if row["status"] == "errore":
        return f"Richiesta #{rid} entro soglia ma il gestionale ha dato errore: {row['result']}"
    return (f"Richiesta #{rid} in attesa di approvazione umana sulla dashboard Varco. "
            f"Verifica l'esito con approval_status({rid}).")


@mcp.tool()
def create(entity: str, data: dict) -> str:
    """Chiede di creare un record. Entro soglia esegue; oltre, serve approvazione umana."""
    try:
        rid = core.request_write(AGENT, "create", entity, None, data)
    except (PermissionError, ValueError) as e:
        raise ToolError(str(e)) from e
    return _esito_richiesta(rid)


@mcp.tool()
def update(entity: str, record_id: str, data: dict) -> str:
    """Chiede di aggiornare un record. Entro soglia esegue; oltre, serve approvazione umana."""
    try:
        rid = core.request_write(AGENT, "update", entity, record_id, data)
    except (PermissionError, ValueError) as e:
        raise ToolError(str(e)) from e
    return _esito_richiesta(rid)


@mcp.tool()
def approval_status(request_id: int) -> str:
    """Stato di una richiesta di scrittura: pending, approvata, rifiutata o errore."""
    with core.db() as c:
        row = c.execute("SELECT status, result FROM approvals WHERE id=?",
                        (request_id,)).fetchone()
    if not row:
        return f"Richiesta {request_id} inesistente"
    return json.dumps({"stato": row["status"], "risultato": row["result"]},
                      ensure_ascii=False)


if __name__ == "__main__":
    if not AGENT:
        raise SystemExit(
            "Imposta VARCO_AGENT con un'identita' registrata in varco_config.json")
    core.agent_perms(AGENT)  # fallisce subito se l'agente non e' registrato
    mcp.run()
