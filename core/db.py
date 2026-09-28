"""SQLite local (stdlib). Fuente de verdad desde Fase A.

Tablas: leagues_cache, rosters_cache, watchlist, claims.
"""

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS leagues_cache (
  league_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT '?',
  waiver_type INTEGER NOT NULL DEFAULT 0,
  budget INTEGER NOT NULL DEFAULT 0,
  bid_min INTEGER NOT NULL DEFAULT 0,
  synced_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rosters_cache (
  league_id TEXT NOT NULL,
  roster_id INTEGER NOT NULL,
  owner_id TEXT,
  players_json TEXT NOT NULL DEFAULT '[]',
  synced_at INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (league_id, roster_id)
);
CREATE TABLE IF NOT EXISTS watchlist (
  player_id TEXT PRIMARY KEY,
  base_bid INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id TEXT NOT NULL,
  league_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  watchlist_priority INTEGER NOT NULL DEFAULT 0,
  base_bid INTEGER NOT NULL DEFAULT 0,
  league_bid INTEGER NOT NULL DEFAULT 0,
  drop_player_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft',
  user_status TEXT NOT NULL DEFAULT 'draft',
  payload_json TEXT NOT NULL DEFAULT '',
  http_status INTEGER,
  sent_at INTEGER,
  error_text TEXT,
  transaction_id TEXT,
  resolved_at INTEGER,
  resolution TEXT,
  checked_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_claims_week_status ON claims(week, status);
CREATE TABLE IF NOT EXISTS fp_wire (
  fp_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  pos TEXT NOT NULL DEFAULT '',
  team TEXT NOT NULL DEFAULT '',
  sleeper_id TEXT,
  rank_ecr INTEGER,
  rank_ave REAL,
  pos_rank TEXT NOT NULL DEFAULT '',
  bye INTEGER,
  owned_avg REAL,
  opp TEXT NOT NULL DEFAULT '',
  tag TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  week INTEGER NOT NULL DEFAULT 0,
  scoring TEXT NOT NULL DEFAULT '',
  fetched_at INTEGER NOT NULL DEFAULT 0
);
"""

def _data_dir():
    """Dir de datos. Default: data/ del repo. Sidecar (Tauri/instalado):
    WAIVERS_DATA_DIR (ej. %APPDATA%/waivers)."""
    explicit = os.environ.get("WAIVERS_DATA_DIR", "")
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


DEFAULT_PATH = os.path.join(_data_dir(), "waiver.db")


def connect(path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.executescript(SCHEMA)
    # Fase 1C: estado 'submitting' eliminado. Sobrevivientes de versiones
    # viejas vuelven a ready (nunca hubo envío a medias visible: el lock
    # real era el commit final del bulk submit).
    cx.execute("UPDATE claims SET status='ready' WHERE status='submitting'")
    cx.commit()
    # Migración ligera: DBs viejas sin checked_at (fase freshness).
    cols = [r[1] for r in cx.execute("PRAGMA table_info(claims)").fetchall()]
    if "checked_at" not in cols:
        cx.execute("ALTER TABLE claims ADD COLUMN checked_at INTEGER")
        cx.commit()
    # Notas de Sleeper (metadata.notes) + ganador (display name).
    if "note" not in cols:
        cx.execute("ALTER TABLE claims ADD COLUMN note TEXT")
        cx.commit()
    if "winner" not in cols:
        cx.execute("ALTER TABLE claims ADD COLUMN winner TEXT")
        cx.commit()
    # fp_wire sin pos_rank/bye (import previo): migrar en sitio.
    fpcols = [r[1] for r in cx.execute("PRAGMA table_info(fp_wire)").fetchall()]
    if fpcols and "pos_rank" not in fpcols:
        cx.execute("ALTER TABLE fp_wire ADD COLUMN pos_rank TEXT NOT NULL DEFAULT ''")
        cx.commit()
    if fpcols and "bye" not in fpcols:
        cx.execute("ALTER TABLE fp_wire ADD COLUMN bye INTEGER")
        cx.commit()
    if fpcols and "scoring" not in fpcols:
        cx.execute("ALTER TABLE fp_wire ADD COLUMN scoring TEXT NOT NULL DEFAULT ''")
        cx.commit()
    return cx
