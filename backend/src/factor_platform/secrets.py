"""The single audited boundary where a configured secret becomes plain text."""

from pydantic import SecretStr


def reveal_secret(secret: SecretStr) -> str:
    """Reveal only at an adapter boundary that immediately transmits the value."""
    return secret.get_secret_value()


__all__ = ["reveal_secret"]
