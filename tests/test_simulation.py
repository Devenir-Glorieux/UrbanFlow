from collections import Counter
from datetime import timedelta

import pytest
from pydantic import ValidationError

from urbanflow.analytics.aggregation import aggregate, spatial_cells
from urbanflow.analytics.baseline import GravityBaseline
from urbanflow.behavior import Activity, ActivityPlan, validate_plan
from urbanflow.common import DomainError
from urbanflow.population import ConfiguredPopulation, building_status
from urbanflow.routing import Router
from urbanflow.simulation import SimulationConfig, Traversal, execute_plan, planning_context
from urbanflow.validation import Observation, metrics, ranks


def test_population_reproducible_and_weighted(world, router, population_config, agents):
    assert agents == ConfiguredPopulation().generate(world, population_config, router)
    assert sum(a.weight for a in agents) == population_config.represented_population
    assert all(a.home_id.startswith("building:") for a in agents)
    assert agents != ConfiguredPopulation().generate(
        world, population_config.model_copy(update={"seed": 43}), router
    )


def test_population_assignment_uses_capacity_without_random_clumping(
    world, router, population_config, agents
):
    residents_by_home = Counter(agent.home_id for agent in agents)
    assert len(residents_by_home) == 5
    assert max(residents_by_home.values()) - min(residents_by_home.values()) <= 1

    unequal_world = world.model_copy(deep=True)
    homes = sorted(
        (obj for obj in unequal_world.objects if obj.kind == "building"), key=lambda obj: obj.id
    )
    for home in homes:
        home.levels = 1
    homes[0].levels = 20
    unequal_agents = ConfiguredPopulation().generate(
        unequal_world,
        population_config.model_copy(update={"size": 100}),
        type(router)(unequal_world),
    )
    unequal_counts = Counter(agent.home_id for agent in unequal_agents)
    assert unequal_counts[homes[0].id] > sum(
        count for home_id, count in unequal_counts.items() if home_id != homes[0].id
    )


def test_population_requires_every_residential_building(world, router, population_config):
    with pytest.raises(DomainError, match="cannot cover 5 residential buildings"):
        ConfiguredPopulation().generate(
            world, population_config.model_copy(update={"size": 4}), router
        )


def test_building_status_uses_current_use_and_lifecycle(world):
    home = next(obj for obj in world.objects if obj.kind == "building")
    assert building_status(home) == "residential"
    converted = home.model_copy(deep=True)
    converted.tags["building:use"] = "commercial"
    assert building_status(converted) == "non_residential"
    converted.tags["abandoned"] = "yes"
    assert building_status(converted) == "abandoned"


def test_no_residential_fallback(world, router, population_config):
    for obj in world.objects:
        if obj.kind == "building":
            obj.category = "yes"
    with pytest.raises(DomainError, match="residential"):
        ConfiguredPopulation().generate(world, population_config, router)


def test_route_respects_direction_and_shortest_path(world, router):
    origin = "building:way/300"
    target = "poi:node/2002"
    path = router.route(origin, target)
    assert len(path) == 8
    assert router.distance(origin, target) > sum(e.length_m for e in path)
    assert router.route(origin, origin) == []
    isolated = world.model_copy(deep=True)
    isolated.edges = [e for e in isolated.edges if e.u != "node/1" and e.v != "node/1"]
    broken = Router(isolated)
    with pytest.raises(DomainError, match="connection"):
        broken.route(origin, target)


def test_planning_context_includes_every_routable_poi(world, agents, sim_config):
    expanded = world.model_copy(deep=True)
    template = next(obj for obj in expanded.objects if obj.kind == "poi")
    expanded.objects.extend(
        template.model_copy(update={"id": f"poi:test/{index}", "osm_id": 50_000 + index})
        for index in range(100)
    )
    expanded_router = Router(expanded)
    context = planning_context(
        expanded.id, agents[0], sim_config.start_date, sim_config, expanded_router
    )
    expected = {obj.id for obj in expanded_router.candidates(agents[0].home_id)}
    assert len(expected) > 80
    assert {candidate.id for candidate in context.candidates} == expected


def test_plan_schema_and_candidate_validation(context):
    home = context.agent.home_id
    with pytest.raises(ValidationError):
        Activity(destination_id=home, minute="10", purpose="home")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ActivityPlan.model_validate({"activities": [], "route": [[1, 2]]})
    invalid = ActivityPlan(
        activities=[
            Activity(destination_id=home, purpose="home", minute=0),
            Activity(destination_id="invented", purpose="food", minute=500),
            Activity(destination_id=home, purpose="home", minute=1000),
        ]
    )
    with pytest.raises(DomainError, match="outside"):
        validate_plan(invalid, context)
    invalid.activities[-1].destination_id = context.candidates[-1].id
    invalid.activities[1].destination_id = home
    with pytest.raises(DomainError, match="start and end"):
        validate_plan(invalid, context)


async def test_baseline_and_execution_reproducible(context, sim_config, router, world):
    baseline = GravityBaseline()
    plan = await baseline.plan(context)
    assert plan == await baseline.plan(context)
    first = execute_plan(plan, context, sim_config, router)
    assert first == execute_plan(plan, context, sim_config, router)
    trips, traversals = first
    assert trips and traversals
    assert all(t.started_at < t.ended_at for t in traversals)
    assert all(t.weight == context.agent.weight for t in traversals)
    assert all(a.ended_at <= b.started_at for a, b in zip(trips, trips[1:], strict=False))
    bins = aggregate(traversals, sim_config.timezone)
    assert sum(b.weighted_count for b in bins) == sum(t.weight for t in traversals)
    counts = {b.edge_id: b.weighted_count for b in bins}
    cells = spatial_cells(world, counts)
    assert sum(c["weighted_count"] for c in cells) == sum(counts.values())


async def test_edge_entry_hour_not_occupancy(context, sim_config, router):
    plan = await GravityBaseline().plan(context)
    _, traversals = execute_plan(plan, context, sim_config, router)
    start = traversals[0].started_at.replace(hour=8, minute=59, second=50, microsecond=0)
    sample = Traversal.model_validate(
        {
            **traversals[0].model_dump(),
            "started_at": start,
            "ended_at": start + timedelta(seconds=30),
        }
    )
    bins = aggregate([sample], sim_config.timezone)
    assert len(bins) == 1 and bins[0].hour.hour == 8
    assert bins[0].weighted_count == sample.weight


def test_late_trip_is_not_teleported(context, sim_config, router):
    far = max(context.candidates, key=lambda c: c.walking_minutes)
    plan = ActivityPlan(
        activities=[
            Activity(destination_id=context.agent.home_id, minute=0, purpose="home"),
            Activity(destination_id=far.id, minute=1438, purpose="leisure"),
            Activity(destination_id=context.agent.home_id, minute=1439, purpose="home"),
        ]
    )
    with pytest.raises(DomainError, match="midnight"):
        execute_plan(plan, context, sim_config, router)


def test_metrics_ties_zeros_and_constant():
    assert ranks([3, 1, 1, 2]) == [4, 1.5, 1.5, 3]
    assert metrics([1, 2, 3], [2, 4, 6]) == {"n": 3, "mae": 2, "spearman": 1}
    assert metrics([1, 2, 3], [3, 2, 1])["spearman"] == -1
    assert metrics([0, 0], [1, 2])["spearman"] is None
    assert metrics([1], [1])["mae"] == 0
    with pytest.raises(DomainError):
        metrics([], [])
    with pytest.raises(DomainError):
        metrics([float("nan")], [1])


def test_observations_require_timezone_and_positive_interval():
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "world_id": "x",
                "edge_id": "e",
                "source": "manual",
                "count": 1,
                "started_at": "2026-09-14T08:00:00",
                "ended_at": "2026-09-14T09:00:00",
            }
        )


def test_dst_boundary_rejected():
    with pytest.raises(ValidationError, match="daylight-saving"):
        SimulationConfig.model_validate(
            {
                "population_id": "x",
                "start_date": "2026-10-24",
                "days": 3,
                "timezone": "Europe/Berlin",
            }
        )
