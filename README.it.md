# Varco

Control plane tra agenti AI ed ERP: **permessi per agente, approvazione umana
su ogni scrittura, audit log completo**. Gli agenti si collegano via MCP
(standard supportato da Claude, Copilot, Cursor e qualsiasi client MCP);
l'ERP si descrive in un file di configurazione — reale via REST, oppure
**finto con dati italiani di esempio** per demo e piloti.

## Architettura

```
agente AI ──MCP──▶ varco_mcp.py ──▶ varco.db (SQLite) ◀── varco_dashboard.py ◀── umano
                       │                                        │
                       └── letture dirette (con audit)          └── scritture SOLO dopo approvazione
                                        ▼
                                  ERP (REST o mock)
```

- [varco_core.py](varco_core.py) — stato, permessi, approvazioni, audit, client ERP (reale/mock)
- [varco_mcp.py](varco_mcp.py) — server MCP verso l'agente (identità da env `VARCO_AGENT`)
- [varco_dashboard.py](varco_dashboard.py) — dashboard di approvazione su http://127.0.0.1:8420
- [varco_config.json](varco_config.json) — ERP, entità, agenti e permessi

## Quickstart

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python test_varco.py
.venv\Scripts\python varco_dashboard.py
```

Collega un agente: il progetto include già [.mcp.json](.mcp.json) con il
server `varco` registrato (identità `assistente-vendite`) — basta aprire
Claude Code in questa cartella e approvare il server al primo avvio.

Su un'altra macchina (o per un'identità diversa) registralo a mano,
adattando i percorsi:

```bash
claude mcp add varco --scope project -e VARCO_AGENT=assistente-vendite -- "C:\percorso\progetto\.venv\Scripts\python.exe" "C:\percorso\progetto\varco_mcp.py"
```

Ogni agente va registrato in `varco_config.json` con i suoi permessi di
lettura/scrittura per entità. Un agente non registrato viene rifiutato al
primo tool call.

## Il flusso che vende

1. L'agente **legge** liberamente ciò che i permessi consentono (tutto in audit).
2. L'agente **chiede** una scrittura (`create`/`update`):
   - **entro la soglia di autonomia** → eseguita subito e tracciata come
     "auto-approvata" (niente approval fatigue sulle routine);
   - **oltre soglia, o senza importo leggibile** → richiesta in coda, ERP non toccato.
3. L'umano vede la richiesta sulla **dashboard** (o su **Telegram**), può
   **correggere i valori** (review-and-edit) e approva o rifiuta.
4. Solo l'approvazione esegue l'azione sull'ERP. Esito tracciato, l'agente lo
   verifica con `approval_status`.

### Soglie di autonomia

Per agente, in `varco_config.json`: `"soglie": {"ordini": 1000}` = esegue da solo
gli ordini fino a €1.000; oltre chiede. Il campo importo di ogni entità è
dichiarato con `"campo_importo"`. Conservativo by design: senza soglia, senza
campo o con valore illeggibile si chiede sempre.

### Approvazioni via Telegram

Con le env `TELEGRAM_BOT_TOKEN` (da @BotFather) e `TELEGRAM_CHAT_ID` impostate,
la dashboard invia ogni richiesta pending come messaggio con bottoni
**Approva / Rifiuta**: si decide con un tap dal telefono, l'esito torna nel
messaggio e nell'audit ("via Telegram"). Senza env, il canale resta spento.
Solo la chat configurata riceve e può decidere. WhatsApp Business è il passo
successivo (richiede approvazione Meta).

## Demo per i titolari (5 minuti, senza ERP reale)

Il config di default punta all'ERP finto (`"base_url": "mock"`) con clienti,
fatture, ordini, fornitori e articoli italiani precaricati. La dashboard è
pensata per persone non tecniche: richieste in linguaggio semplice, processi
a passi, reparti (Vendite, Amministrazione, Acquisti, Magazzino) con i loro
assistenti e le azioni concesse. Copione:

1. Apri la dashboard sulla sezione **I tuoi reparti**: «ogni reparto ha i
   suoi assistenti, ognuno fa solo quello che gli hai concesso».
2. In Claude chiedi: *«quali fatture sono scadute?»* → l'agente le elenca
   (FT-2026-0141 e FT-2026-0163). Mostra l'**Attività** che si popola in
   italiano («Recupero Crediti ha consultato Fatture»).
3. Chiedi: *«crea un ordine per Rossi Costruzioni da 1.500€»* → l'agente
   risponde «in attesa di approvazione umana». **L'ERP non è stato toccato.**
4. In **Da approvare**: la richiesta è una frase con i dati leggibili e il
   processo a passi («Richiesta ricevuta → In attesa di te → Gestionale») →
   clicca Approva → l'ordine esiste. Riprova con Rifiuta: non succede niente.
5. Chiudi con i permessi: il Recupero Crediti non può nemmeno leggere gli
   ordini — provaci e mostra la riga rossa «Richiesta bloccata» in Attività.

Per collegare un ERP vero: in `varco_config.json` sostituisci `"mock"` con
l'URL REST, sistema `auth_format` e i `path` delle entità, token in env
`ERP_API_TOKEN` (esempio incluso in stile ERPNext; Odoo richiede un adapter
JSON-RPC dedicato).

## Limiti noti (v0)

- Dashboard senza login, solo su 127.0.0.1 — auth prima di esporla in rete.
- Un ERP per config — multi-connessione quando servirà il primo cliente multi-ERP.
- Filtri di ricerca semplici (uguaglianza chiave=valore) sull'ERP mock.
