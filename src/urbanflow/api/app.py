from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import create_async_engine

from urbanflow.common import DomainError, Value
from urbanflow.logging import configure_logging
from urbanflow.population import PopulationConfig
from urbanflow.services import UrbanFlow
from urbanflow.settings import Settings
from urbanflow.simulation import SimulationConfig
from urbanflow.storage import schema as s
from urbanflow.storage.repository import Repository
from urbanflow.validation import Observation
from urbanflow.world.models import BoundingBox


class IngestRequest(Value):
    name: str = Field(default="Minsk experiment", min_length=1, max_length=200)
    bbox: BoundingBox


class PopulationRequest(Value):
    world_id: str
    config: PopulationConfig


class CompareRequest(Value):
    baseline_run_id: str
    llm_run_id: str


def service(request: Request) -> UrbanFlow:
    return request.app.state.service


Service = Annotated[UrbanFlow, Depends(service)]


def create_app(injected: UrbanFlow | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = Settings()
        engine = (
            create_async_engine(settings.database_url, pool_pre_ping=True)
            if injected is None
            else None
        )
        if injected is not None:
            app.state.service = injected
        else:
            assert engine is not None
            app.state.service = UrbanFlow(Repository(engine), settings)
        await app.state.service.repo.recover()
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="UrbanFlow", version="0.1.0", lifespan=lifespan)
    if injected is not None:
        app.state.service = injected

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    async def health(svc: Service):
        return await svc.health()

    @app.get("/settings/llm")
    async def llm_defaults(svc: Service):
        return {"provider": svc.settings.llm_provider, "model": svc.settings.llm_model}

    @app.get("/worlds")
    async def worlds(svc: Service):
        return await svc.worlds()

    @app.post("/worlds", status_code=201)
    async def ingest(body: IngestRequest, svc: Service):
        world = await svc.ingest(body.bbox, body.name)
        return world.model_dump(exclude={"objects", "nodes", "edges"})

    @app.get("/worlds/{world_id}/geojson")
    async def world_geojson(world_id: str, svc: Service):
        return await svc.world_geojson(world_id)

    @app.get("/worlds/{world_id}/housing")
    async def housing(world_id: str, svc: Service):
        return await svc.housing(world_id)

    @app.get("/populations")
    async def populations(svc: Service):
        return await svc.repo.list_rows(s.population)

    @app.post("/populations", status_code=201)
    async def population(body: PopulationRequest, svc: Service):
        return await svc.create_population(body.world_id, body.config)

    @app.get("/runs")
    async def runs(svc: Service):
        return await svc.repo.list_rows(s.run)

    @app.post("/runs", status_code=201)
    async def run(body: SimulationConfig, svc: Service):
        return await svc.run(body)

    @app.post("/runs/{run_id}/replay", status_code=201)
    async def replay(run_id: str, svc: Service):
        return await svc.replay(run_id)

    @app.get("/runs/{run_id}/traffic")
    async def traffic(run_id: str, day: date, svc: Service, hour: int = Query(ge=0, le=23)):
        return await svc.traffic(run_id, day, hour)

    @app.get("/runs/{run_id}/trajectories")
    async def trajectories(
        run_id: str,
        day: date,
        svc: Service,
        hour: int = Query(ge=0, le=23),
    ):
        return await svc.trajectories(run_id, day, hour)

    @app.get("/observations")
    async def observations(world_id: str, svc: Service):
        return await svc.observations(world_id)

    @app.post("/observations", status_code=201)
    async def import_observations(
        body: Annotated[list[Observation], Field(max_length=10000)], svc: Service
    ):
        return await svc.import_observations(body)

    @app.post("/validation", status_code=201)
    async def compare(body: CompareRequest, svc: Service):
        return await svc.compare(body.baseline_run_id, body.llm_run_id)

    @app.get("/validation")
    async def validation_results(svc: Service):
        return await svc.repo.list_rows(s.validation_result)

    return app


app = create_app()
