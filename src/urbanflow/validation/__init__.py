"""Observed counts and explicit, comparable pedestrian-count metrics."""

import math
from datetime import datetime

from pydantic import Field, model_validator

from urbanflow.common import DomainError, Value


class Observation(Value):
    world_id: str
    edge_id: str
    started_at: datetime
    ended_at: datetime
    count: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def interval(self) -> Observation:
        if self.started_at.utcoffset() is None or self.ended_at.utcoffset() is None:
            raise ValueError("Observation timestamps need timezone offsets")
        if self.ended_at <= self.started_at:
            raise ValueError("Observation end must be after start")
        return self


def ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and values[ordered[j]] == values[ordered[i]]:
            j += 1
        for index in ordered[i:j]:
            result[index] = (i + 1 + j) / 2
        i = j
    return result


def metrics(observed: list[float], predicted: list[float]) -> dict[str, float | int | None]:
    if not observed or len(observed) != len(predicted):
        raise DomainError("Metrics require equal, nonempty paired measurements")
    if any(not math.isfinite(v) or v < 0 for v in observed + predicted):
        raise DomainError("Counts must be finite and nonnegative")
    n = len(observed)
    a, b = ranks(observed), ranks(predicted)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=False))
    denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return {
        "n": n,
        "mae": sum(abs(x - y) for x, y in zip(observed, predicted, strict=False)) / n,
        "spearman": numerator / denominator if denominator else None,
    }
