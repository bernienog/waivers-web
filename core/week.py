"""Resolucion de leg/week. Semana por parametro (auto-detect post-MVP)."""

from . import sleeper_public as pub


def resolve_leg(league_id: str) -> int:
    lg = pub.get_league(league_id)
    return int((lg.get("settings") or {}).get("leg", 1) or 1)


def current_week_default() -> int:
    # Placeholder: aceptar como param en todas partes. Auto-detect post-MVP.
    return 2
