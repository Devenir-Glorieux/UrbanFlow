from typing import Any, Literal

from pydantic import Field, model_validator

from urbanflow.common import Value

type ObjectKind = Literal["building", "poi", "entrance", "crossing", "transport_stop"]


class BoundingBox(Value):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)

    @model_validator(mode="after")
    def ordered(self) -> BoundingBox:
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("Bounding box must have increasing longitude and latitude")
        if self.east - self.west > 0.05 or self.north - self.south > 0.03:
            raise ValueError("MVP imports are limited to a small experimental area")
        return self


class CityObject(Value):
    id: str
    osm_type: Literal["node", "way", "relation"]
    osm_id: int
    kind: ObjectKind
    geometry: dict[str, Any]
    lon: float
    lat: float
    tags: dict[str, str]
    category: str | None = None
    levels: float | None = None
    opening_hours: str | None = None
    building_id: str | None = None
    node_id: str | None = None


class PedestrianNode(Value):
    id: str
    osm_id: int
    lon: float
    lat: float
    tags: dict[str, str] = Field(default_factory=dict)


class PedestrianEdge(Value):
    id: str
    osm_id: int
    u: str
    v: str
    length_m: float = Field(gt=0)
    geometry: dict[str, Any]
    tags: dict[str, str]
    forward: bool = True
    backward: bool = True


class World(Value):
    id: str
    name: str
    source: str
    bbox: BoundingBox
    objects: list[CityObject]
    nodes: list[PedestrianNode]
    edges: list[PedestrianEdge]
    completeness: dict[str, float | int]
    warnings: list[str] = Field(default_factory=list)
