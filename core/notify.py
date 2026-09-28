"""Discord webhook (Fase D). Solo firma en Fase A."""

WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"


def format_digest_embed(results: dict) -> dict:
    raise NotImplementedError("Fase D")


def send_digest(results: dict, webhook_url: str) -> bool:
    raise NotImplementedError("Fase D")
