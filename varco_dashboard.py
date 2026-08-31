"""App Varco per persone non tecniche.

Avvio: python varco_dashboard.py  ->  http://127.0.0.1:8420
Sidebar con menu multilivello (Reparti -> reparto -> agente, Gestionale ->
dati), richieste in linguaggio semplice, processi a passi. Ogni scrittura
sull'ERP passa da un "Approva" umano.
"""
import asyncio
import contextlib
import csv
import hashlib
import hmac
import html
import io
import json
import os
import time

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

import varco_core as core

# Accesso e deleghe.
# - VARCO_ACCESS_KEY: chiave unica, un solo approvatore ("Tu") su tutti i reparti.
# - VARCO_APPROVERS: "nome:chiave:reparti;..." (reparti separati da virgola, * = tutti)
#   es. "william:kW:*;anna:kA:amministrazione,acquisti" — ogni decisione porta il nome.
# Senza env, demo locale aperta su 127.0.0.1.
ACCESS_KEY = os.environ.get("VARCO_ACCESS_KEY", "")


def _parse_approvers() -> dict:
    out = {}
    for entry in os.environ.get("VARCO_APPROVERS", "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        name, key, depts = entry.split(":", 2)
        out[name] = {"key": key,
                     "depts": None if depts.strip() == "*"
                     else [d.strip() for d in depts.split(",") if d.strip()]}
    return out


APPROVERS = _parse_approvers()


def _approvers() -> dict:
    if APPROVERS:
        return APPROVERS
    if ACCESS_KEY:
        return {"Tu": {"key": ACCESS_KEY, "depts": None}}
    return {}


def _sign(name: str, key: str) -> str:
    return name + "|" + hmac.new(key.encode(), f"varco-session:{name}".encode(),
                                 hashlib.sha256).hexdigest()


def current_approver(request):
    """Nome dell'approvatore loggato; 'Tu' in demo aperta; None se non autenticato."""
    approvers = _approvers()
    if not approvers:
        return "Tu"
    cookie = request.cookies.get("varco", "")
    name = cookie.partition("|")[0]
    a = approvers.get(name)
    if a and hmac.compare_digest(cookie, _sign(name, a["key"])):
        return name
    return None


def authed(request) -> bool:
    return current_approver(request) is not None


def can_decide(name: str, agent_id: str) -> bool:
    depts = _approvers().get(name, {"depts": None})["depts"]
    return depts is None or core.AGENTS.get(agent_id, {}).get("department") in depts

CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:#F6F8F4;color:#16211A;
     font-size:15.5px;line-height:1.55;display:flex;min-height:100vh}
aside{width:242px;flex-shrink:0;background:#1B4231;color:#E8F0EA;
      padding:20px 12px;display:flex;flex-direction:column;gap:2px}
aside .logo{font-weight:800;font-size:20px;letter-spacing:.06em;color:#fff;
            padding:0 10px 14px;border-bottom:1px solid #2E5B47;margin-bottom:10px}
aside .logo span{color:#8FCBA8}
aside a{display:flex;justify-content:space-between;align-items:center;gap:8px;
        color:#CFE0D5;text-decoration:none;padding:7px 10px;border-radius:6px;font-size:14px}
aside a:hover{background:#24523D}
aside a.active{background:#2E5B47;color:#fff;font-weight:600}
aside summary{cursor:pointer;color:#9DBBA9;font-size:11.5px;text-transform:uppercase;
              letter-spacing:.1em;padding:10px 10px 4px}
aside details details summary{text-transform:none;letter-spacing:0;font-size:13.5px;
                              color:#CFE0D5;padding:5px 10px 2px}
aside details details summary a{display:inline;padding:0;color:inherit}
aside details details summary a.active{background:none;color:#fff;font-weight:700}
aside details details a{padding:5px 10px 5px 26px;font-size:13.5px}
aside .count{background:#8FCBA8;color:#13301F;border-radius:10px;font-size:11.5px;
             padding:0 8px;font-weight:700}
aside .foot{margin-top:auto;font-size:11.5px;color:#9DBBA9;padding:12px 10px 0}
main{flex:1;min-width:0;padding:28px 34px 60px;max-width:960px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#56675C;font-size:13.5px;margin:0 0 20px}
h2{font-size:18px;margin:30px 0 12px;display:flex;align-items:center;gap:10px}
h2 .count{font-size:12.5px;background:#22593F;color:#fff;border-radius:11px;padding:1px 10px}
h2 .zero{background:#D7DFD6;color:#56675C}
.card{background:#fff;border:1px solid #D7DFD6;border-radius:8px;padding:18px 20px;margin-bottom:12px}
.req-head{font-size:16px;margin-bottom:4px}
.req-head b{color:#22593F}
.req-when{font-size:12.5px;color:#56675C;margin-bottom:12px}
.kv{display:flex;gap:10px;padding:5px 0;border-bottom:1px dashed #EAEFE8;font-size:14.5px}
.kv:last-child{border-bottom:none}
.kv span{color:#56675C;min-width:130px}
.kv input.edit{font:inherit;font-weight:600;border:1px solid transparent;border-radius:4px;
               padding:1px 6px;background:transparent;flex:1;min-width:0}
.kv input.edit:hover{border-color:#D7DFD6;background:#F6F8F4}
.kv input.edit:focus{border-color:#22593F;background:#fff;outline:none}
.edit-hint{font-size:11.5px;color:#56675C;margin:4px 0 10px}
.kvbox{margin:6px 0 14px}
.autonomy{font-size:12.5px;color:#7A5B0E;background:#FDF3D7;border-radius:4px;
          padding:4px 10px;margin-top:8px;display:inline-block}
details.tech{margin:4px 0 12px}
details.tech summary{font-size:12.5px;color:#56675C;cursor:pointer}
pre{font-family:Consolas,monospace;font-size:12px;background:#F6F8F4;padding:10px;
    border-radius:5px;white-space:pre-wrap;word-break:break-word;margin:6px 0 0}
button{font:inherit;font-size:14.5px;border-radius:6px;padding:9px 22px;cursor:pointer;
       border:1.5px solid #22593F;margin-right:8px}
.approva{background:#22593F;color:#fff;font-weight:600}
.rifiuta{background:#fff;color:#96352B;border-color:#C9A29C}
.empty{color:#56675C;background:#fff;border:1px dashed #D7DFD6;border-radius:8px;
       padding:20px;text-align:center;font-size:14.5px}
.steps{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px}
.step{font-size:12.5px;border-radius:12px;padding:3px 11px;background:#EAEFE8;color:#56675C}
.step.done{background:#E6EFE6;color:#22593F}
.step.stop,.step.err{background:#F8E4E0;color:#96352B}
.step-sep{color:#B9C4BB;font-size:12px}
.proc-title{font-size:14.5px}
.proc-title b{color:#22593F}
.agent-card{background:#fff;border:1px solid #D7DFD6;border-radius:8px;padding:16px 18px;margin-bottom:12px}
.agent-card .name{font-weight:700;font-size:16px}
.agent-card .name a{color:inherit;text-decoration:none}
.agent-card .name a:hover{color:#22593F}
.agent-card .desc{font-size:13.5px;color:#56675C;margin:2px 0 8px}
.agent-card ul{margin:0;padding-left:18px;font-size:14px}
.agent-card li{margin:3px 0}
.agent-card .scope{font-size:12.5px;color:#22593F;margin-top:8px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:4px 0 10px}
.tile{background:#fff;border:1px solid #D7DFD6;border-radius:8px;padding:13px 15px}
.tile .n{font-weight:800;font-size:23px;color:#22593F;font-variant-numeric:tabular-nums}
.tile .l{font-size:12px;color:#56675C;margin-top:2px}
.feed{background:#fff;border:1px solid #D7DFD6;border-radius:8px;padding:6px 18px}
.evt{display:flex;gap:12px;padding:9px 0;border-bottom:1px solid #EFF3EE;font-size:14px;
     align-items:baseline}
.evt:last-child{border-bottom:none}
.evt .t{color:#56675C;font-size:12px;min-width:118px;font-family:Consolas,monospace}
.evt.negato .msg{color:#96352B}
table.data{width:100%;border-collapse:collapse;background:#fff;border:1px solid #D7DFD6}
table.data th,table.data td{text-align:left;padding:10px 14px;border-bottom:1px solid #EAEFE8;
                            font-size:14px;vertical-align:top}
table.data th{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:#56675C;
              font-weight:600;border-bottom:2px solid #16211A}
table.data tr:last-child td{border-bottom:none}
.tablewrap{overflow-x:auto;border-radius:8px}
.warn{background:#F8E4E0;color:#96352B;border-radius:10px;font-size:11.5px;
      padding:1px 8px;margin-left:8px;white-space:nowrap}
@media (max-width:780px){
  body{flex-direction:column}
  aside{width:100%}
  main{padding:20px 16px 50px}
  .kv span{min-width:100px}
  .tiles{grid-template-columns:1fr 1fr}
}
"""

AZIONE_VERBO = {"create": "vuole creare", "update": "vuole modificare"}
CAMPI_EURO = ("totale", "importo", "prezzo")


# ---------------------------------------------------------------- helpers

def agent_label(agent_id: str) -> str:
    if agent_id == "umano":
        return "Tu"
    return core.AGENTS.get(agent_id, {}).get("label", agent_id)


def agent_dept(agent_id: str) -> str:
    dep = core.AGENTS.get(agent_id, {}).get("department", "")
    return core.DEPARTMENTS.get(dep, "")


def ent(entity: str, key: str) -> str:
    return core.ENTITIES.get(entity, {}).get(key, entity)


def fmt_val(k: str, v) -> str:
    if isinstance(v, (int, float)) and k in CAMPI_EURO:
        s = f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
        return f"€ {s}"
    return html.escape(str(v))


def dati_umani(raw: str) -> str:
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return html.escape(raw or "")
    if not isinstance(d, dict):
        return html.escape(raw)
    return "".join(
        f"<div class='kv'><span>{html.escape(k.replace('_', ' ').capitalize())}</span>"
        f"<b>{fmt_val(k, v)}</b></div>" for k, v in d.items())


def dati_editabili(raw: str) -> tuple:
    """Campi del payload come input modificabili (review-and-edit)."""
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return html.escape(raw or ""), False
    if not isinstance(d, dict):
        return html.escape(raw), False
    rows = "".join(
        f"<div class='kv'><span>{html.escape(k.replace('_', ' ').capitalize())}</span>"
        f"<input class='edit' name='f_{html.escape(k, quote=True)}' "
        f"value='{html.escape(str(v), quote=True)}'></div>" for k, v in d.items())
    return rows, True


def extract_edits(form, rid: int):
    """Ricostruisce il payload dai campi del form; None se nulla e' cambiato."""
    with core.db() as c:
        row = c.execute("SELECT payload FROM approvals WHERE id=? AND status='pending'",
                        (rid,)).fetchone()
    if not row:
        return None
    try:
        orig = json.loads(row["payload"])
    except (ValueError, TypeError):
        return None
    if not isinstance(orig, dict):
        return None
    out, changed = {}, False
    for k, v in orig.items():
        nv = form.get(f"f_{k}")
        if nv is None:
            out[k] = v
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                cast = float(nv)
                if isinstance(v, int) and cast.is_integer():
                    cast = int(cast)
            except ValueError:
                cast = v
        else:
            cast = nv
        out[k] = cast
        if cast != v:
            changed = True
    return out if changed else None


def passi(status: str) -> list:
    if status == "pending":
        return [("Richiesta ricevuta", "done"), ("In attesa di te", "step"), ("Gestionale", "step")]
    if status == "approvata":
        return [("Richiesta", "done"), ("Approvata da te", "done"), ("Eseguita sul gestionale", "done")]
    if status == "auto-approvata":
        return [("Richiesta", "done"), ("Entro soglia di autonomia", "done"), ("Eseguita sul gestionale", "done")]
    if status == "rifiutata":
        return [("Richiesta", "done"), ("Rifiutata da te", "stop"), ("Gestionale non toccato", "step")]
    return [("Richiesta", "done"), ("Approvata da te", "done"), ("Errore sul gestionale", "err")]


def steps_html(status: str) -> str:
    parts = []
    for i, (label, cls) in enumerate(passi(status)):
        if i:
            parts.append("<span class='step-sep'>&#8594;</span>")
        parts.append(f"<span class='step {cls}'>{html.escape(label)}</span>")
    return f"<div class='steps'>{''.join(parts)}</div>"


def frase_attivita(r) -> tuple:
    chi = agent_label(r["agent"])
    label = ent(r["entity"], "label")
    sing = ent(r["entity"], "singolare")
    if r["status"] == "negato":
        return f"Richiesta bloccata: {chi} non ha i permessi su {label}", "negato"
    a = r["action"]
    if a in ("search", "get"):
        return f"{chi} ha consultato {label}", ""
    if a == "richiesta-create":
        return f"{chi} ha chiesto di creare {sing} — in attesa di approvazione", ""
    if a == "richiesta-update":
        return f"{chi} ha chiesto di modificare {sing} — in attesa di approvazione", ""
    if a == "approvazione":
        chi_v = "Hai approvato" if chi == "Tu" else f"{chi} ha approvato"
        if r["status"] == "errore":
            return f"{chi_v} {sing}, ma il gestionale ha dato errore", "negato"
        extra = " (con modifiche)" if "modifiche" in (r["detail"] or "") else ""
        return f"{chi_v}: {sing} è stato scritto sul gestionale{extra}", ""
    if a == "auto-approvazione":
        if r["status"] == "errore":
            return f"{chi} ha provato a eseguire {sing} entro soglia, ma il gestionale ha dato errore", "negato"
        return f"{chi} ha eseguito {sing} da solo — entro la sua soglia di autonomia", ""
    if a == "rifiuto":
        chi_v = "Hai rifiutato" if chi == "Tu" else f"{chi} ha rifiutato"
        return f"{chi_v} la richiesta su {label}: nulla è stato toccato", ""
    return f"{chi}: {a} su {label}", ""


# ---------------------------------------------------------------- blocchi

def pending_cards(agent_id: str | None = None, who: str = "Tu") -> tuple:
    with core.db() as c:
        if agent_id:
            rows = c.execute("SELECT * FROM approvals WHERE status='pending' AND agent=? "
                             "ORDER BY id", (agent_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM approvals WHERE status='pending' "
                             "ORDER BY id").fetchall()
    cards = ""
    for r in rows:
        verbo = AZIONE_VERBO.get(r["action"], r["action"])
        target = ent(r["entity"], "singolare")
        rif = f" (n. {html.escape(r['record_id'])})" if r["record_id"] else ""
        campi, editabile = dati_editabili(r["payload"])
        hint = ('<div class="edit-hint">Puoi correggere i valori prima di approvare.</div>'
                if editabile else "")
        if can_decide(who, r["agent"]):
            bottoni = """<button class="approva" name="azione" value="approva">Approva</button>
            <button class="rifiuta" name="azione" value="rifiuta">Rifiuta</button>"""
        else:
            bottoni = (f'<div class="edit-hint">Fuori dalla tua delega: la approva '
                       f'chi segue il reparto {html.escape(agent_dept(r["agent"]))}.</div>')
        cards += f"""<div class="card">
          <div class="req-head"><b>{html.escape(agent_label(r['agent']))}</b>
            &middot; {html.escape(agent_dept(r['agent']))} &mdash; {verbo} <b>{html.escape(target)}</b>{rif}</div>
          <div class="req-when">richiesta n. {r['id']} &middot; {html.escape(r['ts'])}</div>
          <form method="post" action="/decide">
            <input type="hidden" name="id" value="{r['id']}">
            <div class="kvbox">{campi}</div>
            {hint}
            {steps_html('pending')}
            <details class="tech"><summary>Dettagli tecnici</summary><pre>{html.escape(r['payload'] or '')}</pre></details>
            {bottoni}
          </form>
        </div>"""
    if not cards:
        cards = ('<div class="empty">Nessuna richiesta in attesa. '
                 'I tuoi assistenti stanno solo consultando i dati.</div>')
    return cards, len(rows)


def procs_html(agent_id: str | None = None, limit: int = 6) -> str:
    with core.db() as c:
        if agent_id:
            rows = c.execute("SELECT * FROM approvals WHERE status!='pending' AND agent=? "
                             "ORDER BY id DESC LIMIT ?", (agent_id, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM approvals WHERE status!='pending' "
                             "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = ""
    for r in rows:
        verbo = {"create": "creare", "update": "modificare"}.get(r["action"], r["action"])
        out += f"""<div class="card">
          <div class="proc-title"><b>{html.escape(agent_label(r['agent']))}</b>
            ha chiesto di {verbo} {html.escape(ent(r['entity'], 'singolare'))}
            <span style="color:#56675C;font-size:12.5px">&middot; {html.escape(r['decided_ts'] or r['ts'])}</span></div>
          {steps_html(r['status'])}
        </div>"""
    return out or '<div class="empty">Ancora nessun processo completato.</div>'


def feed_html(agent_ids: list | None = None, limit: int = 15) -> str:
    with core.db() as c:
        if agent_ids:
            marks = ",".join("?" * len(agent_ids))
            rows = c.execute(f"SELECT * FROM audit WHERE agent IN ({marks}) "
                             f"ORDER BY id DESC LIMIT ?", (*agent_ids, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
    evts = ""
    for r in rows:
        msg, cls = frase_attivita(r)
        evts += (f"<div class='evt {cls}'><span class='t'>{html.escape(r['ts'])}</span>"
                 f"<span class='msg'>{html.escape(msg)}</span></div>")
    return f"<div class='feed'>{evts}</div>" if evts else \
        '<div class="empty">Nessuna attività ancora.</div>'


def agent_card(aid: str, link: bool = True) -> str:
    a = core.AGENTS[aid]
    azioni = "".join(f"<li>{html.escape(x)}</li>" for x in a.get("azioni", []))
    if a.get("write"):
        scr = ", ".join(ent(e, "label") for e in a["write"])
        scope = f"Può scrivere su: {scr} — sempre con la tua approvazione"
    else:
        scope = "Solo consultazione: non può modificare nulla"
    autonomy = ""
    if a.get("soglie"):
        parti = " e ".join(f"fino a € {v:,.0f}".replace(",", ".") + f" su {ent(e, 'label')}"
                           for e, v in a["soglie"].items())
        extra = ""
        if a.get("soglia_web"):
            partiw = " e ".join(f"€ {v:,.0f}".replace(",", ".") + f" su {ent(e, 'label')}"
                                for e, v in a["soglia_web"].items())
            extra = f"; sopra {partiw} conferma solo dalla dashboard"
        autonomy = (f'<div class="autonomy">Autonomia: esegue da solo {html.escape(parti)}; '
                    f'oltre, chiede a te{html.escape(extra)}</div>')
    name = html.escape(a.get("label", aid))
    if link:
        name = f'<a href="/agente/{aid}">{name}</a>'
    return f"""<div class="agent-card">
      <div class="name">{name}</div>
      <div class="desc">{html.escape(a.get('descrizione', ''))}</div>
      <ul>{azioni}</ul>
      <div class="scope">{html.escape(scope)}</div>
      {autonomy}
    </div>"""


# ---------------------------------------------------------------- layout

def sidebar(path: str, npending: int) -> str:
    def item(href, label, extra=""):
        cls = "active" if path == href else ""
        return f'<a class="{cls}" href="{href}">{html.escape(label)}{extra}</a>'

    reparti = ""
    for dep_id, dep_label in core.DEPARTMENTS.items():
        agents = "".join(item(f"/agente/{aid}", a.get("label", aid))
                         for aid, a in core.AGENTS.items()
                         if a.get("department") == dep_id)
        dep_cls = "active" if path == f"/reparto/{dep_id}" else ""
        reparti += (f'<details open><summary><a class="{dep_cls}" href="/reparto/{dep_id}">'
                    f'{html.escape(dep_label)}</a></summary>{agents}</details>')

    dati = "".join(item(f"/dati/{e}", v.get("label", e))
                   for e, v in core.ENTITIES.items())
    badge = f'<span class="count">{npending}</span>' if npending else ""
    demo = " (demo)" if core.ERP["base_url"] == "mock" else ""
    return f"""<aside>
      <div class="logo">VARCO<span>&#9134;</span></div>
      {item('/', 'Panoramica', badge)}
      <details open><summary>Reparti</summary>{reparti}</details>
      <details open><summary>Gestionale</summary>{dati}</details>
      {item('/attivita', 'Attività')}
      <div class="foot">Gestionale collegato{demo}</div>
    </aside>"""


def layout(title: str, path: str, body: str, refresh: bool = False) -> str:
    with core.db() as c:
        npending = c.execute(
            "SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
    meta = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">{meta}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Varco &mdash; {html.escape(title)}</title><style>{CSS}</style></head><body>
{sidebar(path, npending)}
<main>{body}</main>
</body></html>"""


# ---------------------------------------------------------------- pagine

def stat_tiles() -> str:
    with core.db() as c:
        auto = c.execute("SELECT COUNT(*) FROM approvals WHERE status='auto-approvata'").fetchone()[0]
        appr = c.execute("SELECT COUNT(*) FROM approvals WHERE status='approvata'").fetchone()[0]
        rif = c.execute("SELECT COUNT(*) FROM approvals WHERE status='rifiutata'").fetchone()[0]
    minuti = (auto + appr) * int(os.environ.get("VARCO_MIN_PER_AZIONE", "5"))
    tempo = f"{minuti // 60}h {minuti % 60:02d}m" if minuti >= 60 else f"{minuti}m"
    tiles = [(auto + appr, "azioni eseguite sul gestionale"),
             (auto, "in autonomia, senza disturbarti"),
             (tempo, "tempo risparmiato (stima)"),
             (rif, "fermate da te")]
    return "<div class='tiles'>" + "".join(
        f"<div class='tile'><div class='n'>{v}</div><div class='l'>{l}</div></div>"
        for v, l in tiles) + "</div>"


async def home(request):
    who = current_approver(request)
    if who is None:
        return RedirectResponse("/login", status_code=303)
    cards, n = pending_cards(who=who)
    count_cls = "count" if n else "count zero"
    bulk = ""
    if n > 1:
        bulk = f"""<form method="post" action="/decide_all" style="margin-bottom:12px">
          <button class="approva">Approva tutte ({n})</button></form>"""
    body = f"""
      <h1>Panoramica</h1>
      <p class="sub">Quando un assistente vuole scrivere sul gestionale, prima chiede a te.</p>
      {stat_tiles()}
      <h2>Da approvare <span class="{count_cls}">{n}</span></h2>
      {bulk}
      {cards}
      <h2>Processi recenti</h2>
      {procs_html()}
      <h2>Ultime attivit&agrave;</h2>
      {feed_html(limit=8)}"""
    return HTMLResponse(layout("panoramica", "/", body, refresh=True))


async def reparto(request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    dep_id = request.path_params["dep"]
    if dep_id not in core.DEPARTMENTS:
        return RedirectResponse("/", status_code=303)
    agent_ids = [aid for aid, a in core.AGENTS.items() if a.get("department") == dep_id]
    cards = "".join(agent_card(aid) for aid in agent_ids)
    body = f"""
      <h1>{html.escape(core.DEPARTMENTS[dep_id])}</h1>
      <p class="sub">Gli assistenti di questo reparto e le azioni che possono fare.</p>
      {cards or '<div class="empty">Nessun assistente in questo reparto.</div>'}
      <h2>Attivit&agrave; del reparto</h2>
      {feed_html(agent_ids or ['-'])}"""
    return HTMLResponse(layout(core.DEPARTMENTS[dep_id], f"/reparto/{dep_id}", body))


async def agente(request):
    who = current_approver(request)
    if who is None:
        return RedirectResponse("/login", status_code=303)
    aid = request.path_params["aid"]
    if aid not in core.AGENTS:
        return RedirectResponse("/", status_code=303)
    a = core.AGENTS[aid]
    cards, n = pending_cards(agent_id=aid, who=who)
    count_cls = "count" if n else "count zero"
    body = f"""
      <h1>{html.escape(a.get('label', aid))}</h1>
      <p class="sub">Reparto {html.escape(agent_dept(aid))}</p>
      {agent_card(aid, link=False)}
      <h2>Da approvare <span class="{count_cls}">{n}</span></h2>
      {cards}
      <h2>Processi recenti</h2>
      {procs_html(agent_id=aid)}
      <h2>Attivit&agrave;</h2>
      {feed_html([aid])}"""
    return HTMLResponse(layout(a.get("label", aid), f"/agente/{aid}", body))


async def dati(request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    entity = request.path_params["entity"]
    if entity not in core.ENTITIES:
        return RedirectResponse("/", status_code=303)
    try:
        recs = json.loads(core.erp_read(entity, limit=100))
    except Exception as e:
        recs, err = [], str(e)
    else:
        err = ""
    if not isinstance(recs, list):
        recs = [recs]
    cols = []
    for r in recs:
        for k in r:
            if k not in cols:
                cols.append(k)
    head = "".join(f"<th>{html.escape(c.replace('_', ' ').capitalize())}</th>" for c in cols)
    rows = ""
    for r in recs:
        cells = ""
        for c in cols:
            v = fmt_val(c, r.get(c, ""))
            if (entity == "articoli" and c == "giacenza"
                    and isinstance(r.get(c), (int, float))
                    and isinstance(r.get("scorta_minima"), (int, float))
                    and r[c] < r["scorta_minima"]):
                v += '<span class="warn">Sotto scorta</span>'
            cells += f"<td>{v}</td>"
        rows += f"<tr>{cells}</tr>"
    if err:
        table = f'<div class="empty">Impossibile leggere dal gestionale: {html.escape(err)}</div>'
    elif rows:
        table = f'<div class="tablewrap"><table class="data"><tr>{head}</tr>{rows}</table></div>'
    else:
        table = '<div class="empty">Nessun dato.</div>'
    body = f"""
      <h1>{html.escape(ent(entity, 'label'))}</h1>
      <p class="sub">{html.escape(ent(entity, 'description'))} &mdash; dal gestionale, in sola lettura.</p>
      {table}"""
    return HTMLResponse(layout(ent(entity, "label"), f"/dati/{entity}", body))


async def attivita(request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    body = f"""
      <h1>Attivit&agrave;</h1>
      <p class="sub">Tutto quello che i tuoi assistenti hanno fatto, in ordine di tempo.
        &middot; <a href="/export/audit.csv" style="color:#22593F">Scarica l'audit completo (CSV)</a></p>
      {feed_html(limit=100)}"""
    return HTMLResponse(layout("attività", "/attivita", body, refresh=True))


def login_page(err: str = "") -> str:
    msg = f'<p style="color:#96352B;font-size:14px">{html.escape(err)}</p>' if err else ""
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Varco &mdash; accesso</title><style>{CSS}
.box{{max-width:360px;margin:14vh auto;background:#fff;border:1px solid #D7DFD6;
     border-radius:10px;padding:30px 32px}}
.box input{{font:inherit;width:100%;padding:10px 12px;border:1.5px solid #D7DFD6;
           border-radius:6px;margin:14px 0}}
.box input:focus{{border-color:#22593F;outline:none}}</style></head>
<body style="display:block">
<div class="box">
  <div class="wordmark" style="font-family:'Segoe UI';font-weight:800;letter-spacing:.06em">
    VARCO<span style="color:#22593F">&#9134;</span></div>
  <p style="color:#56675C;font-size:14px;margin:8px 0 0">Inserisci la chiave di accesso.</p>
  {msg}
  <form method="post" action="/login">
    <input type="password" name="chiave" autofocus autocomplete="current-password">
    <button class="approva" style="width:100%">Entra</button>
  </form>
</div></body></html>"""


async def login(request):
    if request.method == "POST":
        form = await request.form()
        chiave = form.get("chiave", "")
        for name, a in _approvers().items():
            if hmac.compare_digest(chiave, a["key"]):
                resp = RedirectResponse("/", status_code=303)
                resp.set_cookie("varco", _sign(name, a["key"]),
                                httponly=True, max_age=30 * 86400)
                return resp
        return HTMLResponse(login_page("Chiave di accesso errata"), status_code=401)
    return HTMLResponse(login_page())


async def decide_all(request):
    who = current_approver(request)
    if who is None:
        return RedirectResponse("/login", status_code=303)
    with core.db() as c:
        rows = c.execute(
            "SELECT id, agent FROM approvals WHERE status='pending' ORDER BY id").fetchall()
    for r in rows:
        if not can_decide(who, r["agent"]):
            continue
        try:
            core.decide(r["id"], True, note=" in blocco", decided_by=who)
        except Exception:  # errori tracciati per singola richiesta
            pass
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


async def decide(request):
    who = current_approver(request)
    if who is None:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    rid = int(form["id"])
    approve = form["azione"] == "approva"
    note = ""
    try:
        with core.db() as c:
            row = c.execute("SELECT agent FROM approvals WHERE id=?", (rid,)).fetchone()
        if row and not can_decide(who, row["agent"]):
            core.audit(who, "decisione", detail=f"richiesta #{rid} fuori delega",
                       status="negato")
        else:
            if approve:
                edits = extract_edits(form, rid)
                if edits is not None:
                    core.update_payload(rid, edits)
                    note = " con modifiche dell'approvatore"
            core.decide(rid, approve, note=note, decided_by=who)
    except Exception:  # esito ed errori restano tracciati in approvals/audit
        pass
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


async def export_audit(request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    with core.db() as c:
        rows = c.execute("SELECT ts, agent, action, entity, record_id, detail, status "
                         "FROM audit ORDER BY id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["quando", "chi", "azione", "entita", "record", "dettaglio", "stato"])
    for r in rows:
        w.writerow(list(r))
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=varco-audit.csv"})


# ---------------------------------------------------------------- Telegram

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def tg_text(r) -> str:
    verbo = AZIONE_VERBO.get(r["action"], r["action"])
    try:
        d = json.loads(r["payload"])
        righe = "\n".join(f"· {k.replace('_', ' ').capitalize()}: {v}" for k, v in d.items())
    except (ValueError, TypeError):
        righe = r["payload"] or ""
    return (f"{agent_label(r['agent'])} {verbo} {ent(r['entity'], 'singolare')}\n"
            f"Richiesta #{r['id']}\n{righe}")


def tg_keyboard(rid: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Approva", "callback_data": f"a:{rid}"},
        {"text": "❌ Rifiuta", "callback_data": f"r:{rid}"},
    ]]}


def tg_urgent(r) -> bool:
    """Sopra soglia_web: notifica subito ma senza one-tap, si conferma dalla dashboard."""
    soglia = core.AGENTS.get(r["agent"], {}).get("soglia_web", {}).get(r["entity"])
    campo = core.ENTITIES.get(r["entity"], {}).get("campo_importo")
    if soglia is None or not campo:
        return False
    try:
        return float(json.loads(r["payload"]).get(campo, 0)) > float(soglia)
    except (ValueError, TypeError):
        return False


def digest_due(hhmm: str, times: list) -> bool:
    return hhmm in times


async def tg_loop():
    api = f"https://api.telegram.org/bot{TG_TOKEN}"
    offset = 0
    digest = [t.strip() for t in os.environ.get("TELEGRAM_DIGEST", "").split(",") if t.strip()]
    async with httpx.AsyncClient(timeout=30) as cl:
        while True:
            try:
                with core.db() as c:
                    rows = c.execute("SELECT * FROM approvals WHERE status='pending' "
                                     "AND notified=0 ORDER BY id").fetchall()
                for r in rows:
                    urgent = tg_urgent(r)
                    if digest and not urgent and not digest_due(time.strftime("%H:%M"), digest):
                        continue  # le richieste ordinarie aspettano il prossimo digest
                    body = {"chat_id": TG_CHAT, "text": tg_text(r)}
                    if urgent:
                        body["text"] += "\n⚠️ Importo alto: conferma dalla dashboard"
                    else:
                        body["reply_markup"] = tg_keyboard(r["id"])
                    await cl.post(f"{api}/sendMessage", json=body)
                    with core.db() as c:
                        c.execute("UPDATE approvals SET notified=1 WHERE id=?", (r["id"],))
                resp = await cl.get(f"{api}/getUpdates",
                                    params={"offset": offset, "timeout": 20})
                for u in resp.json().get("result", []):
                    offset = u["update_id"] + 1
                    cq = u.get("callback_query")
                    if not cq:
                        continue
                    azione, rid = cq.get("data", ":").split(":", 1)
                    try:
                        core.decide(int(rid), azione == "a", note=" via Telegram")
                        esito = "Approvata ed eseguita ✅" if azione == "a" \
                            else "Rifiutata, gestionale non toccato ❌"
                    except Exception as e:
                        esito = f"Non decisa: {e}"
                    await cl.post(f"{api}/answerCallbackQuery",
                                  json={"callback_query_id": cq["id"]})
                    msg = cq.get("message") or {}
                    if msg:
                        await cl.post(f"{api}/editMessageText", json={
                            "chat_id": msg["chat"]["id"],
                            "message_id": msg["message_id"],
                            "text": (msg.get("text") or "") + f"\n\n{esito}"})
            except Exception:
                await asyncio.sleep(5)  # rete giu': si riprova, le richieste restano in coda
            await asyncio.sleep(2)


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(tg_loop()) if TG_TOKEN and TG_CHAT else None
    yield
    if task:
        task.cancel()


app = Starlette(routes=[
    Route("/", home),
    Route("/login", login, methods=["GET", "POST"]),
    Route("/reparto/{dep}", reparto),
    Route("/agente/{aid}", agente),
    Route("/dati/{entity}", dati),
    Route("/attivita", attivita),
    Route("/decide", decide, methods=["POST"]),
    Route("/decide_all", decide_all, methods=["POST"]),
    Route("/export/audit.csv", export_audit),
], lifespan=lifespan)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8420, log_level="warning")
