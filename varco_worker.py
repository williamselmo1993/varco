"""Worker dei workflow gestiti di Varco: documenti in ingresso -> richieste da approvare.

Legge i file nuovi nella inbox di ogni workflow (email .txt/.eml o PDF), chiede al
modello (OpenRouter, default GLM-5.3-Flash) di estrarre i campi dell'entita' e crea
la richiesta in Varco: da li' valgono soglie, review-and-edit, doppia firma e
approvazione come per qualsiasi agente. Ogni richiesta ricorda il modello usato:
le correzioni dell'approvatore diventano il benchmark dei modelli.

Avvio: python varco_worker.py          (loop ogni VARCO_WORKER_SEC, default 30)
       python varco_worker.py --once   (una passata: test, cron)
Env:   OPENROUTER_API_KEY, VARCO_MODEL (default z-ai/glm-5.3-flash)
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

import varco_core as core

MODEL = os.environ.get("VARCO_MODEL", "z-ai/glm-5.3-flash")
WORKFLOWS = core.CONFIG.get("workflows", {})
API = "https://openrouter.ai/api/v1/chat/completions"


def prompt_for(wf: dict) -> str:
    ent = core.ENTITIES[wf["entity"]]
    campi = ", ".join(wf.get("campi", []))
    return (f"Sei un assistente amministrativo italiano. Dal documento estrai "
            f"{ent.get('singolare', 'il record')} e rispondi SOLO con un oggetto JSON "
            f"con questi campi: {campi}. Importi come numeri con punto decimale, senza "
            f"simbolo euro. Se un campo non c'e' usa null. Non inventare. "
            + wf.get("istruzioni", ""))


def call_model(system: str, testo: str, pdf_bytes: bytes | None = None,
               filename: str = "") -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY mancante")
    parts = [{"type": "text", "text": testo or "Documento allegato."}]
    if pdf_bytes:  # i modelli multimodali leggono il PDF direttamente
        parts.append({"type": "file", "file": {
            "filename": filename or "documento.pdf",
            "file_data": "data:application/pdf;base64,"
                         + base64.b64encode(pdf_bytes).decode()}})
    body = {"model": MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": parts}],
            "response_format": {"type": "json_object"},
            # solo provider senza retention: il modello e' aperto, il data path lo scegliamo noi
            "provider": {"data_collection": "deny"}}
    r = httpx.post(API, json=body, timeout=120, headers={
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://github.com/williamselmo1993/varco", "X-Title": "Varco"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < i:
        raise ValueError("nessun JSON nella risposta del modello")
    d = json.loads(text[i:j + 1])
    if not isinstance(d, dict):
        raise ValueError("la risposta del modello non e' un oggetto")
    return d


def _numero(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("€", "").replace(" ", "")
    if "," in s:  # formato italiano 2.480,00
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def normalize(d: dict, wf: dict) -> dict:
    out = {k: d[k] for k in wf.get("campi", []) if d.get(k) is not None}
    campo = core.ENTITIES[wf["entity"]].get("campo_importo")
    if campo in out:
        try:
            out[campo] = _numero(out[campo])
        except ValueError:
            del out[campo]  # importo illeggibile: la richiesta chiedera' sempre approvazione
    return out


def _inbox_dir(wf: dict) -> Path:
    p = Path(wf["inbox"])
    return p if p.is_absolute() else core.BASE_DIR / p


def process_file(wf_name: str, wf: dict, path: Path):
    chiave = f"{wf_name}/{path.name}"
    with core.db() as c:
        if c.execute("SELECT 1 FROM inbox_done WHERE nome=?", (chiave,)).fetchone():
            return None
    pdf = path.read_bytes() if path.suffix.lower() == ".pdf" else None
    testo = "" if pdf else path.read_text(encoding="utf-8", errors="replace")
    rid = None
    try:
        data = normalize(parse_json(call_model(prompt_for(wf), testo, pdf, path.name)), wf)
        if not data:
            raise ValueError("estrazione vuota")
        data["origine"] = path.name
        rid = core.request_write(wf["agent"], "create", wf["entity"], None, data,
                                 modello=MODEL)
        core.audit(wf["agent"], "estrazione", wf["entity"], rid,
                   f"{path.name} -> richiesta #{rid} ({MODEL})")
    except Exception as e:
        core.audit(wf["agent"], "estrazione", wf["entity"], "",
                   f"{path.name}: {e}", "errore")
    with core.db() as c:
        c.execute("INSERT INTO inbox_done (nome, ts, approval_id) VALUES (?,?,?)",
                  (chiave, core.now(), rid))
    return rid


def process_inbox() -> list:
    """Una passata su tutte le inbox; ritorna gli id delle richieste create."""
    creati = []
    for wf_name, wf in WORKFLOWS.items():
        d = _inbox_dir(wf)
        d.mkdir(parents=True, exist_ok=True)
        for f in sorted(p for p in d.iterdir() if p.is_file()):
            rid = process_file(wf_name, wf, f)
            if rid:
                creati.append(rid)
    return creati


if __name__ == "__main__":
    if "--once" in sys.argv:
        print("richieste create:", process_inbox())
    else:
        ogni = int(os.environ.get("VARCO_WORKER_SEC", "30"))
        print(f"Varco worker: {len(WORKFLOWS)} workflow, modello {MODEL}, ogni {ogni}s")
        while True:
            try:
                n = process_inbox()
                if n:
                    print(time.strftime("%H:%M:%S"), "richieste create:", n)
            except Exception as e:  # la rete cade: si riprova al giro dopo
                print("errore:", e)
            time.sleep(ogni)
