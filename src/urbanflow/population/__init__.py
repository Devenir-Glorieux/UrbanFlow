"""Who lives and acts here: configurable, reproducible population generation."""

import random
from bisect import bisect_left
from collections import Counter
from itertools import accumulate
from statistics import median
from typing import Protocol

from pydantic import Field, model_validator
from shapely.geometry import shape
from shapely.ops import transform

from urbanflow.common import DomainError, Value
from urbanflow.routing import Router
from urbanflow.world.models import CityObject, World

RESIDENTIAL_CATEGORIES = {
    "apartments",
    "residential",
    "house",
    "detached",
    "terrace",
    "dormitory",
    "semidetached_house",
}
DEFAULT_LEVELS = {
    "apartments": 5.0,
    "dormitory": 5.0,
    "residential": 3.0,
    "terrace": 2.0,
    "semidetached_house": 2.0,
    "house": 1.0,
    "detached": 1.0,
}
INACTIVE_BUILDING_VALUES = {"abandoned", "construction", "demolished", "proposed", "ruins"}
RESIDENTIAL_USES = RESIDENTIAL_CATEGORIES | {"mixed", "mixed_use"}


class Archetype(Value):
    name: str = Field(min_length=1, max_length=100)
    fraction: float = Field(gt=0, le=1)
    min_age: int = Field(ge=0, le=110)
    max_age: int = Field(ge=0, le=110)
    role: str = Field(pattern="^(worker|student|other)$")
    description: str = Field(max_length=1000)

    @model_validator(mode="after")
    def ages(self) -> Archetype:
        if self.min_age > self.max_age:
            raise ValueError("min_age must not exceed max_age")
        return self


class PopulationConfig(Value):
    size: int = Field(default=20, ge=1, le=1000)
    represented_population: int = Field(default=200, ge=1, le=1_000_000)
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    source: str = "configured assumptions; not measured Minsk demographics"
    archetypes: list[Archetype] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def distribution(self) -> PopulationConfig:
        if abs(sum(a.fraction for a in self.archetypes) - 1) > 1e-6:
            raise ValueError("Archetype fractions must sum to one")
        if len({a.name for a in self.archetypes}) != len(self.archetypes):
            raise ValueError("Archetype names must be unique")
        return self


class Agent(Value):
    id: str
    archetype: str
    age: int
    role: str
    persona: str
    weight: float = Field(gt=0)
    home_id: str
    anchor_id: str | None


class PopulationSource(Protocol):
    def generate(self, world: World, config: PopulationConfig, router: Router) -> list[Agent]: ...


def building_status(building: CityObject) -> str:
    """Classify current residential use from OSM evidence, never from routing proximity."""
    tags = building.tags
    category = building.category or ""
    if category in {"construction", "proposed"} or tags.get("construction") == "yes":
        return "construction"
    if category in INACTIVE_BUILDING_VALUES:
        return "ruins" if category == "demolished" else category
    for status in ("demolished", "removed", "razed", "destroyed", "ruins", "abandoned"):
        if tags.get(status) == "yes" or f"{status}:building" in tags:
            return "ruins" if status in {"demolished", "removed", "razed", "destroyed"} else status
    if tags.get("disused") == "yes" or "disused:building" in tags:
        return "disused"
    current_use = tags.get("building:use")
    if current_use:
        uses = {value.strip() for value in current_use.split(";")}
        return "residential" if uses & RESIDENTIAL_USES else "non_residential"
    if category in RESIDENTIAL_CATEGORIES:
        return "residential"
    return "unknown" if category in {"", "yes"} else "non_residential"


def eligible_homes(world: World) -> list[CityObject]:
    return sorted(
        [
            obj
            for obj in world.objects
            if obj.kind == "building" and building_status(obj) == "residential"
        ],
        key=lambda obj: obj.id,
    )


def residential_capacity(home: CityObject, router: Router) -> float:
    """Return estimated floor area; this is a fallback when flat count is absent."""
    geometry = shape(home.geometry)
    footprint_m2 = transform(router.project.transform, geometry).area
    levels = home.levels or DEFAULT_LEVELS.get(home.category or "", 1.0)
    return max(footprint_m2 * levels, 1.0)


def known_flats(home: CityObject) -> float | None:
    try:
        flats = float(home.tags.get("building:flats", ""))
    except ValueError:
        return None
    return flats if 0 < flats < 10_000 else None


def housing_capacities(homes: list[CityObject], router: Router) -> dict[str, float]:
    """Estimate relative dwelling capacity from flats or calibrated floor area."""
    floor_areas = {home.id: residential_capacity(home, router) for home in homes}
    flats = {home.id: known_flats(home) for home in homes}
    area_per_flat = [
        floor_areas[home.id] / known
        for home in homes
        if (known := flats[home.id]) is not None
    ]
    if not area_per_flat:
        return floor_areas
    typical_area_per_flat = median(area_per_flat)
    return {
        home.id: flats[home.id] or floor_areas[home.id] / typical_area_per_flat for home in homes
    }


def assign_homes(
    homes: list[CityObject], size: int, seed: int, router: Router
) -> list[CityObject]:
    """Cover every residential building, then allocate extra agents by capacity."""
    if size < len(homes):
        raise DomainError(
            f"Population size {size} cannot cover {len(homes)} residential buildings; "
            f"use size >= {len(homes)}"
        )
    rng = random.Random(seed ^ 0x484F4D45)
    assigned = list(homes)
    extra = size - len(homes)
    if extra:
        capacity_by_id = housing_capacities(homes, router)
        cumulative = list(accumulate(capacity_by_id[home.id] for home in homes))
        step = cumulative[-1] / extra
        offset = rng.random() * step
        assigned.extend(
            homes[min(bisect_left(cumulative, offset + index * step), len(homes) - 1)]
            for index in range(extra)
        )
    rng.shuffle(assigned)
    return assigned


def assign_archetypes(
    weights: list[float], archetypes: list[Archetype], represented_population: int, seed: int
) -> list[Archetype]:
    """Keep weighted archetype totals close to configured population fractions."""
    rng = random.Random(seed ^ 0x41524348)
    assigned = [0.0 for _ in archetypes]
    result: list[Archetype | None] = [None] * len(weights)
    order = sorted(range(len(weights)), key=lambda index: (-weights[index], rng.random()))
    targets = [represented_population * archetype.fraction for archetype in archetypes]
    for index in order:
        choice = max(range(len(archetypes)), key=lambda item: targets[item] - assigned[item])
        result[index] = archetypes[choice]
        assigned[choice] += weights[index]
    return [archetype for archetype in result if archetype is not None]


class ConfiguredPopulation:
    def generate(self, world: World, config: PopulationConfig, router: Router) -> list[Agent]:
        rng = random.Random(config.seed)
        homes = eligible_homes(world)
        if not homes:
            raise DomainError("No active residential buildings are identified by OSM tags")
        unroutable = [home for home in homes if home.id not in router.snaps]
        if unroutable:
            raise DomainError(
                f"{len(unroutable)} of {len(homes)} residential buildings cannot reach the "
                "pedestrian graph within 150m; routing data must be fixed before population "
                "generation"
            )
        assigned_homes = assign_homes(homes, config.size, config.seed, router)
        counts = Counter(home.id for home in assigned_homes)
        capacities = housing_capacities(homes, router)
        total_capacity = sum(capacities.values())
        weights = [
            config.represented_population * capacities[home.id] / total_capacity / counts[home.id]
            for home in assigned_homes
        ]
        archetypes = assign_archetypes(
            weights, config.archetypes, config.represented_population, config.seed
        )
        agents = []
        for index, archetype in enumerate(archetypes):
            home = assigned_homes[index]
            candidates = [o for o in router.candidates(home.id) if o.kind == "poi"]
            if archetype.role == "student":
                anchors = [
                    o
                    for o in candidates
                    if o.category in {"amenity:school", "amenity:university", "amenity:college"}
                ]
            elif archetype.role == "worker":
                anchors = [
                    o
                    for o in candidates
                    if (o.category or "").startswith(("office:", "shop:", "amenity:"))
                ]
            else:
                anchors = []
            agents.append(
                Agent(
                    id=f"agent-{index:05d}",
                    archetype=archetype.name,
                    age=rng.randint(archetype.min_age, archetype.max_age),
                    role=archetype.role,
                    persona=archetype.description,
                    weight=weights[index],
                    home_id=home.id,
                    anchor_id=rng.choice(anchors).id if anchors else None,
                )
            )
        agents[-1].weight += config.represented_population - sum(agent.weight for agent in agents)
        return agents
