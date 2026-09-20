from datetime import UTC, date, datetime
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, mapping, shape
from sqlalchemy import Table, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from urbanflow.behavior import Activity, ActivityPlan
from urbanflow.common import DomainError
from urbanflow.population import Agent, PopulationConfig
from urbanflow.storage import schema as s
from urbanflow.world.models import CityObject, PedestrianEdge, PedestrianNode, World


async def insert_many(connection: AsyncConnection, table: Table, rows: list[dict]) -> None:
    for offset in range(0, len(rows), 500):
        await connection.execute(insert(table), rows[offset : offset + 500])


class Repository:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def list_rows(self, table: Table) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = await conn.execute(select(table))
            return [dict(row) for row in rows.mappings()]

    async def require(self, table: Table, identifier: str) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            columns = [column for column in table.c if column.name != "raw_snapshot"]
            row = (
                (await conn.execute(select(*columns).where(table.c.id == identifier)))
                .mappings()
                .first()
            )
        if row is None:
            raise DomainError(f"Unknown {table.name}: {identifier}")
        return dict(row)

    async def save_world(self, world: World, raw: dict) -> None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                pg_insert(s.world)
                .values(
                    id=world.id,
                    name=world.name,
                    source=world.source,
                    bbox=world.bbox.model_dump(),
                    created_at=datetime.now(UTC),
                    raw_snapshot=raw,
                    completeness=world.completeness,
                    warnings=world.warnings,
                )
                .on_conflict_do_nothing()
                .returning(s.world.c.id)
            )
            if result.scalar_one_or_none() is None:
                return
            # Buildings first because POIs and entrances may refer to them.
            for kind in ("building", "poi", "entrance", "crossing", "transport_stop"):
                await insert_many(
                    conn,
                    s.city_object,
                    [
                        {
                            **o.model_dump(exclude={"geometry"}),
                            "world_id": world.id,
                            "geometry": from_shape(shape(o.geometry), srid=4326),
                        }
                        for o in world.objects
                        if o.kind == kind
                    ],
                )
            await insert_many(
                conn,
                s.node,
                [
                    {
                        **n.model_dump(),
                        "world_id": world.id,
                        "geometry": from_shape(Point(n.lon, n.lat), srid=4326),
                    }
                    for n in world.nodes
                ],
            )
            await insert_many(
                conn,
                s.edge,
                [
                    {
                        **e.model_dump(exclude={"geometry"}),
                        "world_id": world.id,
                        "geometry": from_shape(shape(e.geometry), srid=4326),
                    }
                    for e in world.edges
                ],
            )

    async def load_world(self, identifier: str) -> World:
        header = await self.require(s.world, identifier)
        async with self.engine.connect() as conn:
            objects = (
                (
                    await conn.execute(
                        select(s.city_object)
                        .where(s.city_object.c.world_id == identifier)
                        .order_by(s.city_object.c.id)
                    )
                )
                .mappings()
                .all()
            )
            nodes = (
                (
                    await conn.execute(
                        select(s.node).where(s.node.c.world_id == identifier).order_by(s.node.c.id)
                    )
                )
                .mappings()
                .all()
            )
            edges = (
                (
                    await conn.execute(
                        select(s.edge).where(s.edge.c.world_id == identifier).order_by(s.edge.c.id)
                    )
                )
                .mappings()
                .all()
            )
        return World(
            **{k: header[k] for k in ("id", "name", "source", "bbox", "completeness", "warnings")},
            objects=[
                CityObject.model_validate(
                    {
                        **{k: v for k, v in row.items() if k not in {"world_id", "geometry"}},
                        "geometry": mapping(to_shape(row["geometry"])),
                    }
                )
                for row in objects
            ],
            nodes=[
                PedestrianNode.model_validate(
                    {k: v for k, v in row.items() if k not in {"world_id", "geometry"}}
                )
                for row in nodes
            ],
            edges=[
                PedestrianEdge.model_validate(
                    {
                        **{k: v for k, v in row.items() if k not in {"world_id", "geometry"}},
                        "geometry": mapping(to_shape(row["geometry"])),
                    }
                )
                for row in edges
            ],
        )

    async def save_population(
        self, identifier: str, world_id: str, config: PopulationConfig, agents: list[Agent]
    ) -> None:
        async with self.engine.begin() as conn:
            inserted = await conn.execute(
                pg_insert(s.population)
                .values(
                    id=identifier,
                    world_id=world_id,
                    config=config.model_dump(mode="json"),
                    seed=config.seed,
                    source=config.source,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
                .returning(s.population.c.id)
            )
            if inserted.scalar_one_or_none() is None:
                return
            await insert_many(
                conn,
                s.archetype,
                [{"population_id": identifier, **a.model_dump()} for a in config.archetypes],
            )
            await insert_many(
                conn,
                s.agent,
                [
                    {"population_id": identifier, "world_id": world_id, **a.model_dump()}
                    for a in agents
                ],
            )

    async def agents(self, population_id: str) -> list[Agent]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(s.agent)
                    .where(s.agent.c.population_id == population_id)
                    .order_by(s.agent.c.id)
                )
            ).mappings()
            return [
                Agent.model_validate(
                    {k: v for k, v in row.items() if k not in {"population_id", "world_id"}}
                )
                for row in rows
            ]

    async def stored_plans(self, run_id: str) -> dict[tuple[str, date], ActivityPlan]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(s.activity)
                    .where(s.activity.c.run_id == run_id)
                    .order_by(s.activity.c.sequence)
                )
            ).mappings()
            grouped: dict[tuple[str, date], list[Activity]] = {}
            for row in rows:
                grouped.setdefault((row["agent_id"], row["day"]), []).append(
                    Activity(
                        destination_id=row["destination_id"],
                        purpose=row["purpose"],
                        minute=row["minute"],
                    )
                )
        return {key: ActivityPlan(activities=activities) for key, activities in grouped.items()}

    async def recover(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                update(s.run)
                .where(s.run.c.status == "running")
                .values(
                    status="failed",
                    error="Process stopped before results committed; rerun with cache",
                    finished_at=datetime.now(UTC),
                )
            )


class DatabasePlanCache:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def get(self, key: str) -> ActivityPlan | None:
        async with self.engine.connect() as conn:
            response = (
                await conn.execute(select(s.cache.c.response).where(s.cache.c.key == key))
            ).scalar_one_or_none()
        return ActivityPlan.model_validate(response) if response is not None else None

    async def put(
        self, key: str, request: dict[str, Any], plan: ActivityPlan, metadata: dict[str, Any]
    ) -> ActivityPlan:
        async with self.engine.begin() as conn:
            await conn.execute(
                pg_insert(s.cache)
                .values(
                    key=key,
                    request=request,
                    response=plan.model_dump(mode="json"),
                    call_metadata=metadata,
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing()
            )
            response = (
                await conn.execute(select(s.cache.c.response).where(s.cache.c.key == key))
            ).scalar_one()
        return ActivityPlan.model_validate(response)
