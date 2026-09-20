import json
from pathlib import Path

import pytest

from urbanflow.demo import synthetic_osm
from urbanflow.population import ConfiguredPopulation, PopulationConfig
from urbanflow.routing import Router
from urbanflow.simulation import SimulationConfig, planning_context
from urbanflow.world.ingest import transform_osm
from urbanflow.world.models import BoundingBox


@pytest.fixture
def demo_config():
    return json.loads(Path("configs/demo.json").read_text())


@pytest.fixture
def bbox(demo_config):
    return BoundingBox.model_validate(demo_config["bbox"])


@pytest.fixture
def raw(bbox):
    return synthetic_osm(bbox)


@pytest.fixture
def world(raw, bbox):
    return transform_osm(raw, bbox, "test", "SYNTHETIC test")


@pytest.fixture
def router(world):
    return Router(world)


@pytest.fixture
def population_config(demo_config):
    return PopulationConfig.model_validate(demo_config["population"])


@pytest.fixture
def agents(world, router, population_config):
    return ConfiguredPopulation().generate(world, population_config, router)


@pytest.fixture
def sim_config():
    return SimulationConfig(population_id="test")


@pytest.fixture
def context(world, agents, sim_config, router):
    return planning_context(world.id, agents[0], sim_config.start_date, sim_config, router)
