"""Application use cases shared by the API and CLI."""

import asyncio
import logging
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import mapping
from sqlalchemy import func, insert, select, text, update

from urbanflow.analytics.aggregation import aggregate, spatial_cells
from urbanflow.analytics.baseline import GravityBaseline
from urbanflow.behavior import PROMPT_VERSION, ActivityPlan, BehaviorEngine
from urbanflow.behavior.providers import StructuredProvider
from urbanflow.common import DomainError, digest
from urbanflow.population import (
    ConfiguredPopulation,
    PopulationConfig,
    PopulationSource,
    building_status,
    eligible_homes,
    known_flats,
)
from urbanflow.routing import Router
from urbanflow.settings import Settings
from urbanflow.simulation import SimulationConfig, Traversal, Trip, execute_plan, planning_context
from urbanflow.storage import schema as s
from urbanflow.storage.repository import DatabasePlanCache, Repository, insert_many
from urbanflow.validation import Observation, metrics
from urbanflow.world.ingest import fetch_osm, transform_osm
from urbanflow.world.models import BoundingBox, World

log = logging.getLogger(__name__)


class UrbanFlow:
    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        population_source: PopulationSource | None = None,
    ):
        self.repo = repository
        self.settings = settings
        self.population_source = population_source or ConfiguredPopulation()
        # Serialize experiments on this single MVP application instance.
        self.run_lock = asyncio.Lock()

    async def health(self) -> dict:
        async with self.repo.engine.connect() as conn:
            version = (await conn.execute(text("SELECT PostGIS_Version()"))).scalar_one()
            await conn.execute(select(s.world.c.id).limit(1))
        return {"status": "ok", "postgis": version}

    async def worlds(self) -> list[dict]:
        async with self.repo.engine.connect() as conn:
            rows = await conn.execute(
                select(
                    s.world.c.id,
                    s.world.c.name,
                    s.world.c.source,
                    s.world.c.bbox,
                    s.world.c.created_at,
                    s.world.c.completeness,
                    s.world.c.warnings,
                ).order_by(s.world.c.created_at.desc())
            )
            return [dict(row) for row in rows.mappings()]

    async def ingest(
        self,
        bbox: BoundingBox,
        name: str,
        raw: dict | None = None,
        source: str = "OpenStreetMap / Overpass",
    ) -> World:
        data = raw if raw is not None else await fetch_osm(bbox, self.settings.overpass_url)
        world = await asyncio.to_thread(transform_osm, data, bbox, name, source)
        await self.repo.save_world(world, data)
        log.info("world_imported", extra={"world_id": world.id, **world.completeness})
        return world

    async def create_population(self, world_id: str, config: PopulationConfig) -> dict:
        world = await self.repo.load_world(world_id)
        router = await asyncio.to_thread(Router, world)
        agents = self.population_source.generate(world, config, router)
        homes = eligible_homes(world)
        home_counts = Counter(agent.home_id for agent in agents)
        missing_homes = {home.id for home in homes} - set(home_counts)
        if missing_homes:
            raise DomainError(
                f"Generated population leaves {len(missing_homes)} residential buildings "
                "unrepresented"
            )
        identifier = digest({"world": world_id, "config": config.model_dump(), "version": "pop-v3"})
        await self.repo.save_population(identifier, world_id, config, agents)
        log.info("population_generated", extra={"population_id": identifier, "size": len(agents)})
        represented_by_home: dict[str, float] = {}
        for agent in agents:
            represented_by_home[agent.home_id] = (
                represented_by_home.get(agent.home_id, 0) + agent.weight
            )
        return {
            "id": identifier,
            "world_id": world_id,
            "agents": len(agents),
            "represented_population": sum(a.weight for a in agents),
            "eligible_homes": len(homes),
            "occupied_homes": len(home_counts),
            "homes_with_known_flats": sum(known_flats(home) is not None for home in homes),
            "max_agents_in_home": max(home_counts.values()),
            "max_represented_population_in_home": max(represented_by_home.values()),
            "missing_anchors": sum(a.role != "other" and a.anchor_id is None for a in agents),
        }

    async def housing(self, world_id: str) -> dict:
        world = await self.repo.load_world(world_id)
        router = await asyncio.to_thread(Router, world)
        buildings = [obj for obj in world.objects if obj.kind == "building"]
        statuses = Counter(building_status(building) for building in buildings)
        homes = eligible_homes(world)
        routable = [home for home in homes if home.id in router.snaps]
        return {
            "world_id": world_id,
            "building_statuses": dict(sorted(statuses.items())),
            "residential_buildings": len(homes),
            "routable_residential_buildings": len(routable),
            "homes_with_known_flats": sum(known_flats(home) is not None for home in homes),
            "minimum_agents": len(homes),
        }

    async def run(
        self,
        config: SimulationConfig,
        replay_of: str | None = None,
        behavior: BehaviorEngine | None = None,
    ) -> dict:
        async with self.run_lock:
            return await self._run(config, replay_of, behavior)

    async def _run(
        self, config: SimulationConfig, replay_of: str | None, behavior: BehaviorEngine | None
    ) -> dict:
        population = await self.repo.require(s.population, config.population_id)
        world = await self.repo.load_world(population["world_id"])
        agents = await self.repo.agents(config.population_id)
        router = await asyncio.to_thread(Router, world)
        if replay_of is None:
            missing_homes = {home.id for home in eligible_homes(world)} - {
                agent.home_id for agent in agents
            }
            if missing_homes:
                raise DomainError(
                    f"Population leaves {len(missing_homes)} residential buildings "
                    "unrepresented; regenerate it with the current population model"
                )
        saved: dict[tuple[str, date], ActivityPlan] | None = None
        if replay_of:
            original = await self.repo.require(s.run, replay_of)
            if (
                original["status"] != "completed"
                or SimulationConfig.model_validate(original["config"]) != config
            ):
                raise DomainError("Replay requires a completed run with its original configuration")
            saved = await self.repo.stored_plans(replay_of)
        identifier = str(uuid4())
        started = datetime.combine(config.start_date, time(), ZoneInfo(config.timezone))
        row = {
            "id": identifier,
            "world_id": world.id,
            "population_id": config.population_id,
            "mode": config.mode,
            "status": "running",
            "config": config.model_dump(mode="json"),
            "seed": config.seed,
            "provider": config.provider.provider if config.provider else "none",
            "model": config.provider.model if config.provider else "gravity-v1",
            "prompt_version": PROMPT_VERSION if config.mode == "llm" else "gravity-v1",
            "code_version": version("urbanflow"),
            "started_at": started,
            "ended_at": started + timedelta(days=config.days),
            "created_at": datetime.now(UTC),
            "replay_of": replay_of,
        }
        async with self.repo.engine.begin() as conn:
            await conn.execute(insert(s.run).values(**row))
        log.info("simulation_started", extra={"run_id": identifier, "mode": config.mode})
        try:
            plans: list[dict] = []
            activities: list[dict] = []
            trips: list[Trip] = []
            traversals: list[Traversal] = []
            async with httpx.AsyncClient() as client:
                engine: BehaviorEngine = behavior or GravityBaseline()
                if config.mode == "llm" and saved is None and behavior is None:
                    assert config.provider is not None
                    endpoint = self.settings.endpoint_for(config.provider.provider)
                    engine = StructuredProvider(
                        config.provider,
                        endpoint,
                        self.settings.llm_api_key,
                        client,
                        DatabasePlanCache(self.repo.engine),
                    )
                for day_index in range(config.days):
                    day = config.start_date + timedelta(days=day_index)
                    contexts = [planning_context(world.id, a, day, config, router) for a in agents]
                    # Bound live coroutines as well as outbound HTTP concurrency.
                    for offset in range(0, len(contexts), 16):
                        batch = contexts[offset : offset + 16]
                        if saved is not None:
                            daily = [saved[(c.agent.id, day)] for c in batch]
                        else:
                            results = await asyncio.gather(
                                *(engine.plan(c) for c in batch), return_exceptions=True
                            )
                            daily = []
                            for result in results:
                                if isinstance(result, BaseException):
                                    raise result
                                daily.append(result)
                        for context, plan in zip(batch, daily, strict=False):
                            new_trips, new_traversals = execute_plan(plan, context, config, router)
                            trips.extend(new_trips)
                            traversals.extend(new_traversals)
                            plans.append(
                                {
                                    "run_id": identifier,
                                    "population_id": config.population_id,
                                    "agent_id": context.agent.id,
                                    "day": day,
                                }
                            )
                            activities.extend(
                                {
                                    "run_id": identifier,
                                    "world_id": world.id,
                                    "agent_id": context.agent.id,
                                    "day": day,
                                    "sequence": i,
                                    **activity.model_dump(),
                                }
                                for i, activity in enumerate(plan.activities)
                            )
                    log.info(
                        "simulation_day_completed",
                        extra={
                            "run_id": identifier,
                            "day": day.isoformat(),
                            "agents": len(agents),
                        },
                    )
            bins = aggregate(traversals, config.timezone)
            async with self.repo.engine.begin() as conn:
                await insert_many(conn, s.plan, plans)
                await insert_many(conn, s.activity, activities)
                await insert_many(
                    conn, s.trip, [{"run_id": identifier, **t.model_dump()} for t in trips]
                )
                await insert_many(
                    conn,
                    s.traversal,
                    [
                        {"run_id": identifier, "world_id": world.id, **t.model_dump()}
                        for t in traversals
                    ],
                )
                await insert_many(
                    conn,
                    s.traffic,
                    [{"run_id": identifier, "world_id": world.id, **b.model_dump()} for b in bins],
                )
                await conn.execute(
                    update(s.run)
                    .where(s.run.c.id == identifier)
                    .values(
                        status="completed",
                        finished_at=datetime.now(UTC),
                    )
                )
            log.info(
                "simulation_completed",
                extra={
                    "run_id": identifier,
                    "trips": len(trips),
                    "traversals": len(traversals),
                },
            )
        except (Exception, asyncio.CancelledError) as exc:
            # Avoid persisting HTTP URLs/headers or other credential-bearing exception text.
            reason = str(exc) if isinstance(exc, DomainError) else type(exc).__name__
            async with self.repo.engine.begin() as conn:
                await conn.execute(
                    update(s.run)
                    .where(s.run.c.id == identifier)
                    .values(
                        status="failed",
                        finished_at=datetime.now(UTC),
                        error=reason,
                    )
                )
            log.error(
                "simulation_failed", extra={"run_id": identifier, "error_type": type(exc).__name__}
            )
            raise
        return await self.repo.require(s.run, identifier)

    async def replay(self, identifier: str) -> dict:
        original = await self.repo.require(s.run, identifier)
        return await self.run(
            SimulationConfig.model_validate(original["config"]), replay_of=identifier
        )

    async def world_geojson(self, identifier: str) -> dict:
        world = await self.repo.load_world(identifier)
        entrance_buildings = {o.building_id for o in world.objects if o.kind == "entrance"}
        features = [
            {
                "type": "Feature",
                "id": o.id,
                "geometry": o.geometry,
                "properties": {
                    **o.model_dump(exclude={"geometry"}),
                    "known_type": o.category not in {None, "yes", "unknown"},
                    "building_status": building_status(o) if o.kind == "building" else None,
                    "has_entrance": o.id in entrance_buildings,
                    "complete": o.levels is not None and o.category not in {None, "yes"},
                },
            }
            for o in world.objects
        ]
        features.extend(
            {
                "type": "Feature",
                "id": e.id,
                "geometry": e.geometry,
                "properties": {"kind": "pedestrian_graph", **e.model_dump(exclude={"geometry"})},
            }
            for e in world.edges
        )
        return {
            "type": "FeatureCollection",
            "features": features,
            "bbox": [world.bbox.west, world.bbox.south, world.bbox.east, world.bbox.north],
        }

    async def traffic(self, run_id: str, day: date, hour: int) -> dict:
        run = await self.repo.require(s.run, run_id)
        if run["status"] != "completed":
            raise DomainError("Traffic is available only for completed runs")
        config = SimulationConfig.model_validate(run["config"])
        if not config.start_date <= day < config.start_date + timedelta(days=config.days):
            raise DomainError("Day is outside this experiment")
        if not 0 <= hour < 24:
            raise DomainError("Hour must be 0–23")
        timestamp = datetime.combine(day, time(hour), ZoneInfo(config.timezone))
        async with self.repo.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(s.traffic).where(
                        s.traffic.c.run_id == run_id, s.traffic.c.hour == timestamp
                    )
                )
            ).mappings()
            counts = {row["edge_id"]: row["weighted_count"] for row in rows}
        world = await self.repo.load_world(run["world_id"])
        features = [
            {
                "type": "Feature",
                "id": e.id,
                "geometry": e.geometry,
                "properties": {
                    "id": e.id,
                    "weighted_count": counts.get(e.id, 0),
                    "name": e.tags.get("name", ""),
                },
            }
            for e in world.edges
        ]
        return {
            "type": "FeatureCollection",
            "features": features,
            "heatmap": spatial_cells(world, counts),
            "hour": timestamp.isoformat(),
            "total_segment_entries": sum(counts.values()),
        }

    async def trajectories(self, run_id: str, day: date, hour: int) -> dict:
        run = await self.repo.require(s.run, run_id)
        config = SimulationConfig.model_validate(run["config"])
        start = datetime.combine(day, time(hour), ZoneInfo(config.timezone))
        async with self.repo.engine.connect() as conn:
            ids = (
                (
                    await conn.execute(
                        select(s.traversal.c.agent_id)
                        .where(
                            s.traversal.c.run_id == run_id,
                            s.traversal.c.started_at >= start,
                            s.traversal.c.started_at < start + timedelta(hours=1),
                        )
                        .distinct()
                        .order_by(s.traversal.c.agent_id)
                    )
                )
                .scalars()
                .all()
            )
            rows = (
                await conn.execute(
                    select(s.traversal, s.edge.c.geometry)
                    .join(
                        s.edge,
                        (s.edge.c.world_id == s.traversal.c.world_id)
                        & (s.edge.c.id == s.traversal.c.edge_id),
                    )
                    .where(
                        s.traversal.c.run_id == run_id,
                        s.traversal.c.agent_id.in_(ids),
                        s.traversal.c.started_at >= start,
                        s.traversal.c.started_at < start + timedelta(hours=1),
                    )
                )
            ).mappings()
            return {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": mapping(to_shape(row["geometry"])),
                        "properties": {
                            "agent_id": row["agent_id"],
                            "trip_id": row["trip_id"],
                            "started_at": row["started_at"].isoformat(),
                        },
                    }
                    for row in rows
                ],
            }

    async def import_observations(self, observations: list[Observation]) -> dict:
        rows = []
        async with self.repo.engine.begin() as conn:
            for obs in observations:
                geometry = (
                    await conn.execute(
                        select(s.edge.c.geometry).where(
                            s.edge.c.world_id == obs.world_id, s.edge.c.id == obs.edge_id
                        )
                    )
                ).scalar_one_or_none()
                if geometry is None:
                    raise DomainError(f"Observation refers to unknown edge {obs.edge_id}")
                midpoint = to_shape(geometry).interpolate(0.5, normalized=True)
                rows.append(
                    {
                        "id": digest(obs.model_dump(mode="json")),
                        **obs.model_dump(),
                        "geometry": from_shape(midpoint, srid=4326),
                    }
                )
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            for row in rows:
                await conn.execute(pg_insert(s.observation).values(**row).on_conflict_do_nothing())
        return {"accepted": len(rows), "ids": [row["id"] for row in rows]}

    async def observations(self, world_id: str) -> list[dict]:
        async with self.repo.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(*[c for c in s.observation.c if c.name != "geometry"])
                    .where(s.observation.c.world_id == world_id)
                    .order_by(s.observation.c.started_at)
                )
            ).mappings()
            return [dict(row) for row in rows]

    async def compare(self, baseline_id: str, llm_id: str) -> dict:
        baseline = await self.repo.require(s.run, baseline_id)
        llm = await self.repo.require(s.run, llm_id)
        if baseline["mode"] != "baseline" or llm["mode"] != "llm":
            raise DomainError("Select one baseline run and one LLM run")
        if any(run["status"] != "completed" for run in (baseline, llm)):
            raise DomainError("Both runs must be completed")
        for field in ("world_id", "population_id", "started_at", "ended_at"):
            if baseline[field] != llm[field]:
                raise DomainError(f"Comparison requires matching {field}")
        for field in ("timezone", "walking_speed_mps", "seed"):
            if baseline["config"][field] != llm["config"][field]:
                raise DomainError(f"Comparison requires matching {field}")
        observations = [
            o
            for o in await self.observations(baseline["world_id"])
            if o["started_at"] >= baseline["started_at"] and o["ended_at"] <= baseline["ended_at"]
        ]
        if not observations:
            raise DomainError("No observations are fully inside the experiment interval")
        pairs: list[dict[str, Any]] = []
        async with self.repo.engine.connect() as conn:
            for obs in observations:
                predictions = {}
                for label, run_id in (("baseline", baseline_id), ("llm", llm_id)):
                    predictions[label] = float(
                        (
                            await conn.execute(
                                select(func.coalesce(func.sum(s.traversal.c.weight), 0)).where(
                                    s.traversal.c.run_id == run_id,
                                    s.traversal.c.edge_id == obs["edge_id"],
                                    s.traversal.c.started_at >= obs["started_at"],
                                    s.traversal.c.started_at < obs["ended_at"],
                                )
                            )
                        ).scalar_one()
                    )
                pairs.append({"observation_id": obs["id"], "observed": obs["count"], **predictions})
        observed = [p["observed"] for p in pairs]
        baseline_metrics = metrics(observed, [p["baseline"] for p in pairs])
        llm_metrics = metrics(observed, [p["llm"] for p in pairs])
        identifier = str(uuid4())
        async with self.repo.engine.begin() as conn:
            await conn.execute(
                insert(s.validation_result).values(
                    id=identifier,
                    baseline_run_id=baseline_id,
                    llm_run_id=llm_id,
                    created_at=datetime.now(UTC),
                    n=len(pairs),
                    baseline_mae=baseline_metrics["mae"],
                    baseline_spearman=baseline_metrics["spearman"],
                    llm_mae=llm_metrics["mae"],
                    llm_spearman=llm_metrics["spearman"],
                )
            )
            await insert_many(
                conn, s.validation_pair, [{"result_id": identifier, **p} for p in pairs]
            )
        return {
            "id": identifier,
            "baseline": baseline_metrics,
            "llm": llm_metrics,
            "pairs": pairs,
            "note": "Spearman is null for fewer than two ranks or constant counts.",
        }
