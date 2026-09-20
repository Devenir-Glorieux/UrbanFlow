"""Geographic helpers for selecting an exact 1 km² import area."""

from typing import Any

from pyproj import CRS, Transformer

from urbanflow.world.models import BoundingBox

AREA_SIDE_M = 1_000.0


def square_bbox(latitude: float, longitude: float, side_m: float = AREA_SIDE_M) -> BoundingBox:
    """Return a locally projected square centered at the supplied WGS84 point."""
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m")
    to_wgs84 = Transformer.from_crs(local, 4326, always_xy=True)
    half = side_m / 2
    west, south = to_wgs84.transform(-half, -half)
    east, north = to_wgs84.transform(half, half)
    return BoundingBox(west=west, south=south, east=east, north=north)


def bbox_feature(bbox: BoundingBox) -> dict[str, Any]:
    """Return the persisted import boundary as a GeoJSON polygon."""
    return {
        "type": "Feature",
        "id": "world-boundary",
        "properties": {"id": "Imported 1 km² boundary", "kind": "world_boundary"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [bbox.west, bbox.south],
                    [bbox.east, bbox.south],
                    [bbox.east, bbox.north],
                    [bbox.west, bbox.north],
                    [bbox.west, bbox.south],
                ]
            ],
        },
    }
