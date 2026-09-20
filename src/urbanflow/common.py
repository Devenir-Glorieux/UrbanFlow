"""Small shared value and error helpers; no service state."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DomainError(ValueError):
    """An actionable invalid experiment input."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def stable_seed(*parts: object) -> int:
    return int(digest(parts)[:16], 16)
