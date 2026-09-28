"""Validación pre-ready. Pura (sin red): recibe claim + datos ya resueltos."""

from .models import Claim


def validate_claim(claim: Claim, *, mode: str, roster_players: list,
                   budget_left: int, bid_min: int,
                   rostered_all: set | None = None,
                   free_slots: int = 0,
                   locked_drops: set | None = None) -> tuple[bool, str]:
    """Retorna (valid, reason). reason='' si valid.
    Sin drop solo pasa con slot libre real (Sleeper lo permite y lo marca);
    fail-closed por default para no cambiar callers viejos.
    locked_drops: ocupantes de taxi/IR — Sleeper rechaza dropearlos en
    un claim, así que se bloquean aquí antes de tocar red."""
    roster_players = [str(p) for p in (roster_players or [])]
    if not claim.drop_player_id:
        if int(free_slots or 0) <= 0:
            return False, "Sin drop elegido"
    else:
        if str(claim.drop_player_id) not in roster_players:
            return False, "Drop no esta en tu roster"
        if str(claim.drop_player_id) == str(claim.player_id):
            return False, "No puedes dropear al mismo que reclamas"
        if locked_drops and str(claim.drop_player_id) in locked_drops:
            return False, "Drop en taxi/IR (Sleeper lo rechaza)"
    if rostered_all is not None and str(claim.player_id) in rostered_all:
        return False, "Add no es agente libre (tiene dueño en la liga)"
    if mode == "faab":
        bid = int(claim.league_bid or 0)
        if bid < int(bid_min or 0):
            return False, f"Bid {bid} < minimo {bid_min}"
        if bid <= 0 and int(bid_min or 0) > 0:
            return False, "Liga FAAB pero bid=0"
        if bid > int(budget_left or 0):
            return False, f"Bid {bid} > saldo {budget_left}"
    return True, ""
