import asyncio
import logging
from typing import Any

import httpx
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import polygonize, transform, unary_union

from urbanflow.common import DomainError, digest
from urbanflow.world.models import (
    BoundingBox,
    CityObject,
    ObjectKind,
    PedestrianEdge,
    PedestrianNode,
    World,
)

TRANSFORM_VERSION = "osm-v1"
log = logging.getLogger(__name__)
WALKABLE = {
    "footway",
    "pedestrian",
    "path",
    "steps",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "track",
    "tertiary_link",
    "secondary_link",
    "primary_link",
    "road",
    "cycleway",
}


def projection(bbox: BoundingBox) -> Transformer:
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={(bbox.south + bbox.north) / 2} "
        f"+lon_0={(bbox.west + bbox.east) / 2} +datum=WGS84 +units=m"
    )
    return Transformer.from_crs(4326, crs, always_xy=True)


def pedestrian_access(tags: dict[str, str]) -> bool:
    foot = tags.get("foot")
    if foot in {"no", "private", "use_sidepath"}:
        return False
    if tags.get("access") in {"no", "private"} and foot not in {"yes", "designated", "permissive"}:
        return False
    if tags.get("area") == "yes" or tags.get("highway") not in WALKABLE:
        return False
    if tags.get("highway") == "cycleway" and foot not in {"yes", "designated", "permissive"}:
        return False
    return True


async def fetch_osm(bbox: BoundingBox, endpoint: str) -> dict[str, Any]:
    bounds = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
    filters = [
        "building",
        "amenity",
        "shop",
        "office",
        "leisure",
        "tourism",
        "entrance",
        "highway",
        "public_transport",
        "railway",
    ]
    query = (
        "[out:json][timeout:90];("
        + "".join(f'nwr["{key}"]({bounds});' for key in filters)
        + ");(._;>>;);out body;"
    )
    async with httpx.AsyncClient(
        timeout=120,
        headers={
            "User-Agent": "UrbanFlow/0.1 (pedestrian research)",
            "Accept": "application/json",
        },
    ) as client:
        for attempt in range(3):
            try:
                response = await client.post(endpoint, data={"data": query})
                response.raise_for_status()
                data = response.json()
                if data.get("remark") or not isinstance(data.get("elements"), list):
                    raise DomainError("Overpass returned incomplete data; retry a smaller area")
                log.info("osm_downloaded", extra={"elements": len(data["elements"])})
                return data
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and (
                    exc.response.status_code < 500 and exc.response.status_code not in {408, 429}
                ):
                    raise DomainError(
                        f"Overpass rejected the import (HTTP {exc.response.status_code}); "
                        "retry later or configure OVERPASS_URL"
                    ) from exc
                if attempt == 2:
                    raise DomainError(
                        "Overpass unavailable after three attempts; retry later"
                    ) from exc
                await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


def transform_osm(raw: dict[str, Any], bbox: BoundingBox, name: str, source: str) -> World:
    """Transform a complete Overpass snapshot; no network or mutable global settings."""
    elements = raw.get("elements", [])
    if not isinstance(elements, list) or len(elements) > 500_000:
        raise DomainError("Invalid or oversized OSM snapshot")
    points = {e["id"]: e for e in elements if e.get("type") == "node"}
    ways = {e["id"]: e for e in elements if e.get("type") == "way"}
    boundary = box(bbox.west, bbox.south, bbox.east, bbox.north)
    project = projection(bbox)
    objects: list[CityObject] = []
    edges: list[PedestrianEdge] = []
    nodes: dict[str, PedestrianNode] = {}
    warnings: list[str] = []

    def coords(way: dict[str, Any]) -> list[tuple[float, float]]:
        refs = way.get("nodes", [])
        if any(ref not in points for ref in refs):
            return []  # Never bridge gaps left by missing source nodes.
        return [(points[n]["lon"], points[n]["lat"]) for n in refs]

    relation_building_members: set[int] = set()
    for element in sorted(elements, key=lambda e: (e.get("type", ""), e.get("id", 0))):
        tags = {str(k): str(v) for k, v in element.get("tags", {}).items()}
        osm_type, osm_id = element["type"], element["id"]
        geometry = None
        if osm_type == "node":
            geometry = Point(element["lon"], element["lat"])
        elif osm_type == "way":
            xy = coords(element)
            if len(xy) >= 4 and xy[0] == xy[-1]:
                geometry = Polygon(xy)
            elif len(xy) >= 2:
                geometry = LineString(xy)
        elif osm_type == "relation" and tags.get("type") == "multipolygon":
            rings: dict[str, list[LineString]] = {"outer": [], "inner": []}
            incomplete = False
            members: list[int] = []
            for member in element.get("members", []):
                if member["type"] != "way":
                    continue
                xy = coords(ways.get(member["ref"], {}))
                role = member.get("role") or "outer"
                if role not in rings:
                    continue
                if len(xy) < 2:
                    incomplete = True
                else:
                    rings[role].append(LineString(xy))
                    members.append(member["ref"])
            if not incomplete and rings["outer"]:
                outer = unary_union(list(polygonize(rings["outer"])))
                inner = unary_union(list(polygonize(rings["inner"])))
                geometry = outer.difference(inner)
                if tags.get("building"):
                    relation_building_members.update(members)
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            if tags.get("building") or tags.get("highway"):
                warnings.append(f"Skipped incomplete/invalid {osm_type}/{osm_id}")
            continue
        if not geometry.intersects(boundary):
            continue
        identifier = f"{osm_type}/{osm_id}"
        center = geometry.representative_point()
        kinds: list[tuple[ObjectKind, str | None]] = []
        if tags.get("building") not in {None, "no"}:
            if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                kinds.append(("building", tags["building"]))
        for key in ("amenity", "shop", "office", "leisure", "tourism"):
            if key in tags:
                kinds.append(("poi", f"{key}:{tags[key]}"))
                break
        if "entrance" in tags and tags["entrance"] != "no":
            kinds.append(("entrance", tags["entrance"]))
        if tags.get("highway") == "crossing":
            kinds.append(("crossing", tags.get("crossing", "unknown")))
        if (
            tags.get("public_transport") in {"platform", "stop_position", "station"}
            or tags.get("highway") == "bus_stop"
            or tags.get("railway") in {"tram_stop", "station", "halt"}
        ):
            kinds.append(("transport_stop", tags.get("public_transport", "stop")))
        for kind, category in kinds:
            levels = None
            try:
                value = float(tags.get("building:levels", ""))
                if 0 < value < 200:
                    levels = value
            except ValueError:
                pass
            objects.append(
                CityObject(
                    id=f"{kind}:{identifier}",
                    osm_type=osm_type,
                    osm_id=osm_id,
                    kind=kind,
                    geometry=mapping(geometry),
                    lon=center.x,
                    lat=center.y,
                    tags=tags,
                    category=category,
                    levels=levels,
                    opening_hours=tags.get("opening_hours"),
                )
            )
        if osm_type != "way" or not pedestrian_access(tags):
            continue
        xy = coords(element)
        refs = element["nodes"]
        if len(xy) != len(refs):
            continue
        for index in range(len(refs) - 1):
            u, v = refs[index : index + 2]
            line = LineString(xy[index : index + 2])
            # Keep complete intersecting segments, including their boundary endpoints.
            if u == v or not line.intersects(boundary):
                continue
            # A private/no-access node or impassable barrier must not connect paths.
            blocked = False
            for ref in (u, v):
                nt = points[ref].get("tags", {})
                if (
                    nt.get("foot") in {"no", "private"}
                    or (
                        nt.get("access") in {"no", "private"}
                        and nt.get("foot") not in {"yes", "designated", "permissive"}
                    )
                    or nt.get("barrier") in {"wall", "fence", "retaining_wall"}
                ):
                    blocked = True
            if blocked:
                continue
            length = transform(project.transform, line).length
            if length <= 0:
                continue
            for ref in (u, v):
                node = points[ref]
                nodes[f"node/{ref}"] = PedestrianNode(
                    id=f"node/{ref}",
                    osm_id=ref,
                    lon=node["lon"],
                    lat=node["lat"],
                    tags=node.get("tags", {}),
                )
            direction = tags.get("oneway:foot", "no")
            edges.append(
                PedestrianEdge(
                    id=f"way/{osm_id}/{index}",
                    osm_id=osm_id,
                    u=f"node/{u}",
                    v=f"node/{v}",
                    geometry=mapping(line),
                    length_m=length,
                    tags=tags,
                    forward=direction != "-1",
                    backward=direction not in {"yes", "1", "true"},
                )
            )
    objects = [
        o
        for o in objects
        if not (
            o.kind == "building" and o.osm_type == "way" and o.osm_id in relation_building_members
        )
    ]
    if not edges:
        raise DomainError("Snapshot contains no usable pedestrian edges")
    buildings = [o for o in objects if o.kind == "building"]
    projected = {o.id: transform(project.transform, shape(o.geometry)) for o in objects}
    for obj in objects:
        if obj.kind not in {"entrance", "poi"}:
            continue
        matches = [(projected[b.id].distance(projected[obj.id]), b.id) for b in buildings]
        if matches:
            distance, building_id = min(matches)
            if distance <= (3 if obj.kind == "entrance" else 25):
                obj.building_id = building_id
    total = len(buildings)

    def percent(count):
        return round(100 * count / total, 2) if total else 0.0

    entrances = {o.building_id for o in objects if o.kind == "entrance"}
    pois = {o.building_id for o in objects if o.kind == "poi"}
    stats = {
        "buildings": total,
        "pois": sum(o.kind == "poi" for o in objects),
        "nodes": len(nodes),
        "edges": len(edges),
        "known_type_pct": percent(sum(b.category not in {"yes", "unknown"} for b in buildings)),
        "known_levels_pct": percent(sum(b.levels is not None for b in buildings)),
        "with_entrances_pct": percent(sum(b.id in entrances for b in buildings)),
        "with_nearby_pois_pct": percent(sum(b.id in pois for b in buildings)),
    }
    return World(
        id=digest({"raw": raw, "bbox": bbox.model_dump(), "version": TRANSFORM_VERSION}),
        name=name,
        source=source,
        bbox=bbox,
        objects=objects,
        nodes=sorted(nodes.values(), key=lambda n: n.id),
        edges=sorted(edges, key=lambda e: e.id),
        completeness=stats,
        warnings=warnings,
    )
