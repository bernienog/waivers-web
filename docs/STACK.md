# Stack — waivers-project

Local-first Sleeper waivers tool. No cloud, no Docker, no CI. Two processes
on localhost + one SQLite file.

```
web/ (React, :5173) ── /api/* proxy ──► api/main.py (FastAPI, :3001)
                                              │         │
                                     core/sleeper_public  core/sleeper_private
                                      (REST, anonymous)   (GraphQL + JWT)
                                              │         │
                                              ▼         ▼
                                     api.sleeper.app   sleeper.com/graphql
                                              │
                                              ▼
                                     data/waiver.db (SQLite, source of truth)
```

## Backend — Python

| Piece | Version (verified 2026-09-24) | Notes |
|---|---|---|
| Python | 3.14.0 | `python --version` |
| FastAPI | 0.128.0 | `api/main.py`, thin wrappers over `core/` |
| Uvicorn | 0.40.0 | `python -m uvicorn api.main:app --port 3001`, no autoreload |
| pydantic | via FastAPI | request bodies (`QuickClaim`, `PickupBody`, `CancelBody`, …) |
| requests | >=2.31 | never called directly — always via `core/fetch.py` |
| python-dotenv | >=1.0 | loads `.env` server-side only |
| sqlite3 | stdlib | `core/db.py`, file `data/waiver.db` |
| streamlit | >=1.30 | legacy `app.py` fallback client only, not in the main flow |

Key backend modules (`core/`):

- `fetch.py` — **all** outbound Sleeper traffic goes through here.
  Global lock, `MIN_GAP=0.15`s + jitter. Call meter at `GET /api/health`.
- `sleeper_public.py` — anonymous REST (`api.sleeper.app`):
  leagues, rosters, players map, transactions, `waiver_mode`,
  `remaining_budget`, `roster_space`, `get_rostered_ids`.
- `sleeper_private.py` — authenticated GraphQL (`sleeper.com/graphql` + JWT):
  `list_pending`, submit / cancel / update / reorder, `get_waiver_clears`,
  `get_projections`. Pending reads share a 30s TTL cache, busted on writers.
- `validator.py` — pure pre-`ready` check (no network): drop ∈ roster,
  drop ≠ add, taxi/IR not droppable, add must be free, `bid_min ≤ bid ≤ saldo`.
- `db.py` — schema + light migrations. Tables: `leagues_cache`,
  `rosters_cache`, `watchlist`, `claims`, `fp_wire`.
- `verify.py` — Tier-1 reconcile: `submitted` vs Sleeper, writes local DB only.
- `digest.py` / `week.py` / `fantasypros.py` / `notify.py` — morning-after
  digest, week default + `leg`, FP wire ETL, Discord webhook signature.

## Frontend — React

| Piece | Version (`web/package.json`) | Notes |
|---|---|---|
| node / npm | v24.14.1 / 11.11.0 | verified live |
| react / react-dom | ^19.2.8 | `web/src/` |
| vite | ^8.3.0 | dev `:5173`, proxy `/api → localhost:3001` (`vite.config.ts`) |
| @vitejs/plugin-react | ^6.1.1 | |
| typescript | ~6.0.2 | `npm run build` = `tsc -b && vite build` (typecheck gate) |
| @dnd-kit/core ^6.3.1, sortable ^10.0.0, utilities ^3.2.2 | drag-drop pending reorder + watchlist |
| oxlint | ^1.81.0 | `npm run lint` |

Frontend layout (`web/src/`):

- `App.tsx` — topbar, tabs (`hub` | `dashboard`), global ↻ (verify + leagues
  + freshness), league selector, JWT/stale banners, claim modal host.
- `api.ts` — single fetch wrapper, talks to `/api/*` only. Emits
  `fp:claims-changed` after sends (one event per batch).
- `components/Hub.tsx` — home: pending per league, ready queue, FP wire,
  watchlist, digest + history. One refresh wave per action (`refreshMany`,
  500ms debounce in wire panel).
- `components/LeagueClaimModal.tsx` — new claim wizard (FA search + drop +
  bid → `ready`), single + multibid with instant send.
- `components/FreeAgentsPanel.tsx` / `RosterPanel.tsx` — dashboard columns.
- `components/PendingDND.tsx` / `WatchlistDND.tsx` / `BidModal.tsx` /
  `PlayerBits.tsx` — reorder, bid edit, chips/avatars.

## Data — SQLite

File: `data/waiver.db` (auto-created, `connect()` runs schema + migrations).

- `claims` — lifecycle `draft → ready → submitting → submitted → failed → resolved`
  (+ `week`, `league_bid`, `drop_player_id`, `transaction_id`, `resolution`,
  `checked_at`, `note`, `winner`).
- `watchlist` — (`player_id`, `base_bid`, `priority`, `notes`).
- `fp_wire` — FantasyPros snapshot (`fp_id`, ECR ranks, `sleeper_id`, `week`, …).
- `leagues_cache` / `rosters_cache` — display caches.

## External services

| Service | Use | Auth |
|---|---|---|
| `api.sleeper.app` (public REST) | leagues, rosters, players, transactions | none |
| `sleeper.com/graphql` (private) | pending reads, submit/cancel/update/reorder, clears, projections | `SLEEPER_JWT` + `sleeper-web-session` cookie, server-side only (`.env`, never logged/committed) |
| FantasyPros waiver-wire page | ECR consensus ETL into `fp_wire` | none (scrape, read-only) |
| Discord webhook | digest embed (signature fixed, Fase D) | webhook URL, not wired yet |

## Ports & environments

| Process | Command | Port |
|---|---|---|
| Backend | `python -m uvicorn api.main:app --port 3001` | 3001 (restart required, no autoreload) |
| Frontend | `npm run dev` in `web/` | 5173 (hot-reload) |
| API docs | browser | `localhost:3001/docs` |
| Health / call meter | `GET /api/health` | `{ok, sleeper_calls}` |

`.env` keys (values never committed): `SLEEPER_JWT`, `SLEEPER_SESSION`,
`SLEEPER_USER_ID`, `SEASON`.

## Domain facts that constrain the stack

- `waiver_type==2` → FAAB, `0/1` → claim-by-priority. Claim leagues show
  budget `n/a` (Sleeper's `waiver_budget` there is a meaningless default).
- Week numbering runs +1 vs Sleeper: our week N reconciles Sleeper N + N−1.
- `waiver_clears_at` is seconds (siblings are ms, normalize `<1e12 *1000`);
  stale on rollover → display only, never a hard gate. Only hard gate: rostered.
- `transaction_id` unique per claim; pending lives only behind private GraphQL.
- FAAB always sends `waiver_bid` incl. `$0`; dropless submits use empty drops.
