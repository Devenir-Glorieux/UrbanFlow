"""Clock, deterministic execution, and persisted trajectory values."""

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from urbanflow.behavior import ActivityPlan, Candidate, PlanningContext, validate_plan
from urbanflow.behavior.providers import ProviderConfig
from urbanflow.common import DomainError, Value, stable_seed
from urbanflow.population import Agent
from urbanflow.routing import Router


class SimulationConfig(Value):
    population_id: str
    mode: Literal["baseline", "llm"] = "baseline"
    start_date: date = date(2026, 9, 14)
    days: int = Field(default=7, ge=1, le=31)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    timezone: str = "Europe/Minsk"
    walking_speed_mps: float = Field(default=1.3, ge=0.3, le=3)
    provider: ProviderConfig | None = None

    @model_validator(mode="after")
    def valid(self) -> SimulationConfig:
        if self.mode == "llm" and self.provider is None:
            raise ValueError("LLM runs require a provider and model")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone") from exc
        # Minute-of-day plans currently assume uniform local days.
        zone = ZoneInfo(self.timezone)
        offsets = {
            datetime.combine(self.start_date + timedelta(days=i), time(), zone).utcoffset()
            for i in range(self.days + 1)
        }
        if len(offsets) != 1:
            raise ValueError("MVP runs cannot cross a daylight-saving offset transition")
        return self


class Trip(Value):
    id: str
    agent_id: str
    day: date
    sequence: int
    origin_id: str
    destination_id: str
    purpose: str
    started_at: datetime
    ended_at: datetime
    distance_m: float


class Traversal(Value):
    trip_id: str
    sequence: int
    agent_id: str
    edge_id: str
    started_at: datetime
    ended_at: datetime
    weight: float


def planning_context(
    world_id: str, agent: Agent, day: date, config: SimulationConfig, router: Router
) -> PlanningContext:
    candidates = router.candidates(agent.home_id)
    # Supply every routable destination; retain a deterministic, useful order.
    ordered = sorted(
        candidates,
        key=lambda o: (
            o.id not in {agent.home_id, agent.anchor_id},
            router.distance(agent.home_id, o.id),
            o.id,
        ),
    )
    return PlanningContext(
        world_id=world_id,
        agent=agent,
        day=day,
        seed=stable_seed(config.seed, agent.id, day.isoformat()),
        candidates=[
            Candidate(
                id=o.id,
                category=o.category,
                name=o.tags.get("name", ""),
                opening_hours=o.opening_hours,
                walking_minutes=round(
                    router.distance(agent.home_id, o.id) / config.walking_speed_mps / 60, 2
                ),
            )
            for o in ordered
        ],
    )


def execute_plan(
    plan: ActivityPlan, context: PlanningContext, config: SimulationConfig, router: Router
) -> tuple[list[Trip], list[Traversal]]:
    validate_plan(plan, context)
    start = datetime.combine(context.day, time(), ZoneInfo(config.timezone))
    available = start
    trips: list[Trip] = []
    traversals: list[Traversal] = []
    for index, (previous, activity) in enumerate(
        zip(plan.activities, plan.activities[1:], strict=False)
    ):
        if previous.destination_id == activity.destination_id:
            continue
        trip_id = f"{context.agent.id}/{context.day}/{index}"
        departure = max(available, start + timedelta(minutes=activity.minute))
        speed = config.walking_speed_mps
        cursor = departure + timedelta(seconds=router.snaps[previous.destination_id][1] / speed)
        edges = router.route(previous.destination_id, activity.destination_id)
        for sequence, edge in enumerate(edges):
            end = cursor + timedelta(seconds=edge.length_m / speed)
            traversals.append(
                Traversal(
                    trip_id=trip_id,
                    sequence=sequence,
                    agent_id=context.agent.id,
                    edge_id=edge.id,
                    started_at=cursor,
                    ended_at=end,
                    weight=context.agent.weight,
                )
            )
            cursor = end
        available = cursor + timedelta(seconds=router.snaps[activity.destination_id][1] / speed)
        if available >= start + timedelta(days=1):
            raise DomainError("Plan cannot finish before midnight at the configured walking speed")
        trips.append(
            Trip(
                id=trip_id,
                agent_id=context.agent.id,
                day=context.day,
                sequence=index,
                origin_id=previous.destination_id,
                destination_id=activity.destination_id,
                purpose=activity.purpose,
                started_at=departure,
                ended_at=available,
                distance_m=router.distance(previous.destination_id, activity.destination_id),
            )
        )
    return trips, traversals
