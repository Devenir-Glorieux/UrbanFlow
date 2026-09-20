"""Provider-independent daily activity planning."""

from datetime import date
from typing import Protocol

from pydantic import Field, model_validator

from urbanflow.common import DomainError, Value
from urbanflow.population import Agent

PROMPT_VERSION = "daily-plan-v4"


class Activity(Value):
    destination_id: str
    purpose: str = Field(pattern="^(home|work|study|food|shopping|leisure|other)$")
    minute: int = Field(ge=0, le=1439, strict=True)


class ActivityPlan(Value):
    activities: list[Activity] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def chronological(self) -> ActivityPlan:
        minutes = [a.minute for a in self.activities]
        if minutes != sorted(set(minutes)) or minutes[0] != 0:
            raise ValueError("Activities must start at minute 0 and increase strictly")
        return self


class Candidate(Value):
    id: str
    category: str | None
    name: str
    opening_hours: str | None
    walking_minutes: float


class PlanningContext(Value):
    world_id: str
    agent: Agent
    day: date
    seed: int
    candidates: list[Candidate]


def validate_plan(plan: ActivityPlan, context: PlanningContext) -> ActivityPlan:
    valid = {c.id for c in context.candidates}
    if any(a.destination_id not in valid for a in plan.activities):
        raise DomainError("Plan references a destination outside the supplied candidates")
    first, last = plan.activities[0], plan.activities[-1]
    if any(a.destination_id != context.agent.home_id or a.purpose != "home" for a in (first, last)):
        raise DomainError("Every daily plan must start and end at the agent's home")
    return plan


class BehaviorEngine(Protocol):
    async def plan(self, context: PlanningContext) -> ActivityPlan: ...
