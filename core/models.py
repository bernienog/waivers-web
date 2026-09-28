"""Dominio compartido: watchlist + claims. Sin I/O, sin red."""

from dataclasses import dataclass, field

# Lifecycle de un claim. Fase 1C: 'submitting' eliminado (era solo un
# lock visible del bulk submit; la protección vive en memoria en api).
DRAFT = "draft"
READY = "ready"
SUBMITTED = "submitted"
FAILED = "failed"
RESOLVED = "resolved"

CLAIM_STATUSES = (DRAFT, READY, SUBMITTED, FAILED, RESOLVED)

# Resultado morning-after
WON = "won"
LOST = "lost"
UNKNOWN = "unknown"


@dataclass
class WatchlistItem:
    player_id: str
    base_bid: int = 0
    priority: int = 0  # menor = mas prioritario (reemplaza DND con orden explícito)
    notes: str = ""


@dataclass
class Claim:
    player_id: str
    league_id: str
    week: int
    drop_player_id: str
    league_bid: int = 0  # 0 = claim normal (sin FAAB); >0 = bid FAAB
    watchlist_priority: int = 0
    base_bid: int = 0
    status: str = DRAFT
    user_status: str = DRAFT
    payload_json: str = ""
    http_status: int | None = None
    sent_at: int | None = None
    error_text: str | None = None
    transaction_id: str | None = None
    resolved_at: int | None = None
    resolution: str | None = None
    id: int | None = None


def is_faab_claim(claim: Claim) -> bool:
    return int(claim.league_bid or 0) > 0
