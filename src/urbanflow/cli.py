import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from urbanflow.demo import synthetic_osm
from urbanflow.logging import configure_logging
from urbanflow.population import PopulationConfig
from urbanflow.services import UrbanFlow
from urbanflow.settings import Settings
from urbanflow.simulation import SimulationConfig
from urbanflow.storage.repository import Repository
from urbanflow.world.models import BoundingBox


def migrate() -> None:
    command.upgrade(Config("alembic.ini"), "head")


async def demo(path: str, real_osm: bool) -> None:
    config = json.loads(Path(path).read_text())
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        svc = UrbanFlow(Repository(engine), settings)
        bbox = BoundingBox.model_validate(config["bbox"])
        world = await svc.ingest(
            bbox,
            config["name"] if real_osm else "SYNTHETIC offline demonstration",
            raw=None if real_osm else synthetic_osm(bbox),
            source="OpenStreetMap / Overpass" if real_osm else "SYNTHETIC fixture v1; not real OSM",
        )
        population = await svc.create_population(
            world.id, PopulationConfig.model_validate(config["population"])
        )
        result = await svc.run(
            SimulationConfig(population_id=population["id"], **config["simulation"])
        )
        print(
            json.dumps(
                {
                    "world_id": world.id,
                    "population_id": population["id"],
                    "baseline_run_id": result["id"],
                    "source": world.source,
                },
                indent=2,
            )
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="UrbanFlow development and experiment commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("migrate")
    sub.add_parser("init-env")
    demo_parser = sub.add_parser("demo")
    demo_parser.add_argument("--config", default="configs/demo.json")
    demo_parser.add_argument("--real-osm", action="store_true")
    post = sub.add_parser("request", help="Send a JSON file to the local API")
    post.add_argument("path", help="API path, e.g. /runs or /observations")
    post.add_argument("file", help="JSON request file")
    args = parser.parse_args()
    configure_logging()
    if args.command == "migrate":
        migrate()
    elif args.command == "serve":
        migrate()
        uvicorn.run("urbanflow.api.app:app", host="0.0.0.0", port=8000, access_log=False)
    elif args.command == "demo":
        asyncio.run(demo(args.config, args.real_osm))
    elif args.command == "init-env":
        target = Path(".env")
        if target.exists():
            print(".env already exists; left unchanged")
        else:
            target.write_text(Path(".env.example").read_text())
            target.chmod(0o600)
            print("Created .env")
    elif args.command == "request":
        with httpx.Client(timeout=3600) as client:
            response = client.post(
                os.getenv("URBANFLOW_API_URL", "http://localhost:8000") + args.path,
                json=json.loads(Path(args.file).read_text()),
            )
            print(response.text)
            response.raise_for_status()


if __name__ == "__main__":
    main()
