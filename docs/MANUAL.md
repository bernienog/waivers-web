# Manual de uso — waivers-project

Herramienta local para waivers multi-liga de Sleeper: digest/verify, Hub,
import FantasyPros, claims single + multibid con envío instantáneo.

Stack técnico: ver [STACK.md](STACK.md). UI en español, terse.

## 1. Arranque

Requisitos: Python 3.14, Node 24 + npm 11 (ver STACK.md para versiones
exactas). Dos terminales, en la raíz del repo salvo `web/` donde se indica.

```bash
# 1) Backend (puerto 3001). Reiniciar SIEMPRE tras cambiar backend: sin autoreload.
python -m uvicorn api.main:app --port 3001

# 2) Frontend (puerto 5173, hot-reload)
cd web
npm install   # solo la primera vez
npm run dev
```

Abrir:

- UI visual → http://localhost:5173
- API interactiva → http://localhost:3001/docs
- Health + contador de llamadas → http://localhost:3001/api/health

### 1.1 `.env` (una vez)

Copiar `.env.example` a `.env` y llenar. Nunca commitear, nunca loguear.

| Key | Para qué |
|---|---|
| `SLEEPER_JWT` | Auth privada (GraphQL). Sin JWT fresco no hay envío ni lectura de pendings. Re-farmear cuando el banner diga `JWT expired/missing/invalid` o `expira`. |
| `SLEEPER_SESSION` | Cookie `sleeper-web-session` que acompaña al JWT. |
| `SLEEPER_USER_ID` | Tu user id (dueño de los rosters). |
| `SEASON` | `2026`. |

Verificación sin tocar Sleeper:

```bash
python -c "import api.main"
cd web && npm run build   # typecheck + build
```

## 2. Recorrido de la UI

Dos pestañas (no refetchean al cambiar; solo ↻ refresca):

- **⌂ inicio (Hub)** — centro de mando: pendings por liga, cola `ready`,
  panel FantasyPros wire, watchlist, digest + historial.
- **Mis Ligas (dashboard)** — una liga a la vez: `RosterPanel` (tu roster,
  taxi/IR marcados no-dropeables) + `FreeAgentsPanel` (buscador FA con
  proyección semanal desc por default, o rank).

Topbar global:

- Selector de liga: `nombre · record · modo · saldo`.
- **↻ global**: verify (Tier-1, solo-lectura remota) + recarga ligas +
  freshness. Úsalo al abrir la app y tras cada slate.
- **↻ por liga** (badge en dashboard y filas del Hub): verify scopeado +
  refresh de esa liga. Barato; preferirlo al global.
- Banners: `JWT …` (re-farmea), `N submitted sin verificar` (pulsa verificar),
  `sin auditoría` (backend viejo → reinicia uvicorn).

## 3. Flujo semanal recomendado

1. **Abrir y verificar.** ↻ global. Lee el flash: `verify: N vigentes · …`.
2. **Importar FantasyPros** (Hub → panel wire → importar). Cruza ECR con el
   mapa Sleeper; SOS se ignora. Un snapshot por semana en `fp_wire`.
3. **Elegir targets.** En el wire o en Mis Ligas → `+` sobre un FA abre el
   modal de claim. El `+` se bloquea si el jugador tiene dueño en esa liga
   o si ya tienes claim abierto propio (`taken` + `pending` del availability
   matrix). El modal re-gatea antes de enviar de todos modos.
4. **Crear claim** (modal): elige liga(s), drop (o sin drop si hay slot
   libre), bid (solo FAAB; claim ignora bid). Esto crea fila `ready` en DB
   local — **no toca Sleeper todavía**. Triple puerta: `ready` → re-chequeo
   pre-send → validación del servidor.
5. **Enviar.** Desde el Hub (cola ready): envío simple (yolo, un claim) o
   **multibid** (lista cerrada de ids, secuencial con jitter). Cada envío
   exitoso devuelve `transaction_id` y pasa a `submitted`.
6. **Gestionar pendings.** En el Hub: cancelar (drop), editar bid (FAAB,
   in-place mismo tid), reordenar priority (ligas claim, drag-drop). Todo
   revienta el cache de pendings de 30s.
7. **Morning-after.** ↻ global o digest: `submitted` se reconcilian a
   `won/lost`, historial por semana, export CSV.

## 4. Referencia por acción

| Quiero… | Dónde | Qué pasa por debajo |
|---|---|---|
| Ver saldo / prioridad / slots | Topbar, badge de liga | `GET /api/leagues`, `GET /api/leagues/{id}`. Claim = saldo `n/a`. |
| Buscar FA / ver proyección | Mis Ligas → FreeAgentsPanel | `GET /api/free-agents/{lid}?q=&pos=&sort=proj\|rank`. Default top-150 por rank + dropeados recientes con clears. |
| Ver mi roster | Mis Ligas → RosterPanel | `GET /api/leagues/{lid}/roster`. Taxi/IR expuestos para filtrar (no dropeables). |
| Crear claim suelto | `+` en FA, o modal | `POST /api/claims/quick` → valida → `ready`. Sin drop solo con slot libre real. |
| Marcar ready | Cola review | `POST /api/claims/{cid}/ready`. 422 con razón si inválido (drop ajeno, bid < min, bid > saldo, add con dueño…). |
| Enviar uno | Hub → enviar | `POST /api/claims/{cid}/send`. Writer: dispara a Sleeper de verdad. |
| Enviar varios (multibid) | Hub → multibid | `POST /api/claims/send-many {ids}`. Secuencial + jitter 3.5–5.5s. Solo esos ids. |
| Cancelar pending | Hub → drop en la fila | `POST /api/pending/cancel {league_id, transaction_id, leg}`. Marca local a `failed`. Guard anti-dobletap: la fila se deshabilita hasta reconciliar. |
| Editar bid | Hub → editar | `POST /api/pending/update {bid}`. Piso `bid_min`; sincroniza `league_bid` local. |
| Reordenar (claim) | Hub → drag-drop | `POST /api/pending/reorder {ordered_ids}`. Una mutación por claim, para al primer fallo. |
| Pickup FA libre (sin lock) | Modal → pickup | `POST /api/free-agents/pickup`. Completa al instante, sin pending. |
| Abandonar claim huérfano | cola submitted | `POST /api/claims/{cid}/abandon`. Solo DB local (cancelado fuera de la herramienta). |
| Verificar | ↻ | `POST /api/verify [{league_id}]`. Solo-lectura remota; escribe DB local. |
| Digest / historial | Hub → anoche / historial | `GET /api/digest?week=`, `GET /api/history`. 100% local, cero Sleeper. Semana nuestra N = Sleeper N + N−1 (header verbatim). |
| Exportar CSV | — | `GET /api/export/ready\|submitted`. |
| Fecha exacta de clear | badge en filas | `GET /api/waiver-clears/{lid}` (cache 15min; `?fresh=1` revienta, `?past=1` incluye vencidos). Display only — el lock rancio de rollover jamás bloquea; el gate duro es rostered. |

### Estados de un claim

`draft → ready → submitting → submitted → resolved (won/lost)`,
`failed` en cualquier punto con `error_text`. `checked_at` estampa el verify;
sin él, `submitted` viejo (>12h) levanta el banner stale.

## 5. Reglas de seguridad (leer antes de enviar)

- **Writers = fuego real.** Enviar, pickup, cancelar, editar bid, reordenar:
  mutan Sleeper. La herramienta nunca live-firea sola; cada writer nace de
  tu clic. Revisa liga + add + drop + bid antes de confirmar.
- **JWT vive en el servidor.** Si caduca, los reads siguen (públicos) pero
  los writers y pendings fallan con banner explícito.
- **Tráfico educado por construcción.** Todo sale por `core/fetch.py`
  (`MIN_GAP=0.15`s + jitter). No paralelizar a mano; optimizar cortando
  llamadas, no añadiendo concurrencia.
- **Ligas claim no tienen FAAB.** Bid siempre 0; FAAB siempre manda
  `waiver_bid` aunque sea `$0` (sin bid Sleeper lo lee como claim).

## 6. Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| Backend sirve código viejo ("server fantasma") | PID viejo en :3001 | Mata el proceso en :3001, levanta uvicorn de nuevo. |
| `sin auditoría` / freshness no responde | backend viejo o caído | Reinicia `uvicorn … --port 3001`. |
| `JWT expired/missing/invalid` | JWT rancio o ausente | Re-farmea `SLEEPER_JWT` (+ cookie) en `.env`, reinicia backend. |
| `N submitted sin verificar` persistente | slate sin reconciliar | ↻ global (verify). Si un tid murió fuera de la herramienta → abandonar. |
| 422 al crear/ready/enviar | validator o pre-send | Lee la razón: drop en taxi/IR, add con dueño, bid < min / > saldo, sin slots. Corrige y reintenta. |
| `+` bloqueado en un FA | `taken` o claim propio abierto | Correcto por diseño; verifica en la liga si lo dropearon. |
| Clears muestra fecha pasada pero sigue lockeado | rollover rancio de Sleeper | Display only: intenta el envío, Sleeper valida al momento. |
| Orden pending raro | API devuelve newest-first | La UI espeja `settings.priority` asc (claim). FAAB (`waiver_bid`, sin priority) respeta orden API. |
| Frontend no ve backend | proxy / backend caído | Backend en 3001 primero; `vite.config.ts` proxea `/api`. Error en UI indica el comando exacto. |

## 7. Dónde está cada cosa (mapa rápido)

- `api/main.py` — todas las rutas (landing en `/`, docs en `/docs`).
- `core/` — `fetch` (pacing), `sleeper_public` (REST), `sleeper_private`
  (GraphQL), `validator`, `db`, `verify`, `digest`, `week`, `fantasypros`.
- `web/src/` — `App`, `api` (único cliente HTTP), `components/Hub`,
  `LeagueClaimModal`, `FreeAgentsPanel`, `RosterPanel`, resto DND/bits.
- `data/waiver.db` — verdad local (backups `*.bak-*` conviven al lado).
- `AGENTS.md` — reglas de agentes (cero llamadas Sleeper sin autorización).
- `NOTES.md` — constraints duros del MVP. `plans/ROADMAP.md` — roadmap vivo.
