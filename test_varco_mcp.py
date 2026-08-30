"""Check end-to-end sul VERO protocollo MCP: varco_mcp.py come subprocess stdio.

Il client (SDK mcp 2.x) lancia il server con identita' diverse (VARCO_AGENT) e
verifica: elenco tool, lettura, flusso di approvazione umana e permesso negato.
Il DB e' un file temporaneo (VARCO_DB) impostato PRIMA di importare varco_core,
cosi' il varco.db demo del progetto non viene mai toccato.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.path.join(tempfile.mkdtemp(prefix="varco_mcp_test_"), "varco_test.db")
os.environ["VARCO_DB"] = DB_PATH  # prima dell'import di varco_core
sys.path.insert(0, str(BASE_DIR))

import varco_core as core  # noqa: E402

from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

PYTHON = str(BASE_DIR / ".venv" / "Scripts" / "python.exe")
SERVER = str(BASE_DIR / "varco_mcp.py")
EXPECTED_TOOLS = {"list_entities", "search", "get", "create", "update", "approval_status"}


def server_params(agent: str) -> StdioServerParameters:
    # Su Windows il subprocess ha bisogno dell'ambiente completo (SystemRoot ecc.):
    # si passa tutto os.environ piu' identita' agente e DB temporaneo.
    env = {**os.environ, "VARCO_AGENT": agent, "VARCO_DB": DB_PATH}
    return StdioServerParameters(command=PYTHON, args=[SERVER], env=env, cwd=str(BASE_DIR))


def text_of(result) -> str:
    return "".join(b.text for b in result.content if getattr(b, "type", "") == "text")


async def main() -> None:
    # --- agente con permessi di lettura e scrittura su ordini -----------------
    async with Client(server_params("assistente-vendite")) as client:
        # 1) initialize + tools/list
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == EXPECTED_TOOLS, f"tool inattesi: {sorted(names)}"
        print(f"[1] tools/list ok: {sorted(names)}")

        # 2) tools/call search su fatture
        res = await client.call_tool("search", {"entity": "fatture"})
        assert not res.is_error, f"search fatture in errore: {text_of(res)}"
        body = text_of(res)
        assert "FT-2026-0141" in body, f"FT-2026-0141 assente: {body}"
        print("[2] search fatture ok: trovata FT-2026-0141")

        # 3) create su ordini -> richiesta pending con id
        res = await client.call_tool("create", {
            "entity": "ordini",
            "data": {"numero": "OD-2026-0999", "cliente": "Rossi Costruzioni Srl",
                     "totale": 1999.0, "stato": "bozza"}})  # sopra soglia: resta pending
        assert not res.is_error, f"create ordini in errore: {text_of(res)}"
        reply = text_of(res)
        m = re.search(r"Richiesta #(\d+) in attesa", reply)
        assert m, f"nessun id richiesta pending nella risposta: {reply}"
        rid = int(m.group(1))
        print(f"[3] create ordini ok: richiesta pending #{rid}")

        # 4) approvazione umana fuori banda (stesso VARCO_DB del server)
        outcome = json.loads(core.decide(rid, True))
        assert outcome["esito"] == "creato", outcome
        print(f"[4] decide({rid}, True) ok: {outcome}")

        # 5) approval_status via protocollo -> approvata
        res = await client.call_tool("approval_status", {"request_id": rid})
        assert not res.is_error, f"approval_status in errore: {text_of(res)}"
        status = json.loads(text_of(res))
        assert status["stato"] == "approvata", status
        print(f"[5] approval_status ok: {status}")

    # --- agente in sola lettura: ordini gli sono negati -----------------------
    async with Client(server_params("sollecitatore")) as client:
        res = await client.call_tool("search", {"entity": "ordini"})
        assert res.is_error, "il sollecitatore non doveva poter leggere gli ordini"
        denial = text_of(res)
        assert "permesso" in denial.lower(), f"messaggio di negazione assente: {denial}"
        print(f"[6] permesso negato ok: {denial}")

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
