"""Explicitly synthetic OSM-format fixture, never represented as real city data."""

from typing import Any

from urbanflow.world.models import BoundingBox


def synthetic_osm(bbox: BoundingBox) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    dx, dy = (bbox.east - bbox.west) / 6, (bbox.north - bbox.south) / 6
    for y in range(5):
        for x in range(5):
            elements.append(
                {
                    "type": "node",
                    "id": 1 + y * 5 + x,
                    "lon": bbox.west + (x + 1) * dx,
                    "lat": bbox.south + (y + 1) * dy,
                    "tags": {"highway": "crossing"} if (x, y) == (2, 2) else {},
                }
            )
    for y in range(5):
        elements.append(
            {
                "type": "way",
                "id": 100 + y,
                "nodes": [1 + y * 5 + x for x in range(5)],
                "tags": {"highway": "footway", "name": f"Synthetic row {y}"},
            }
        )
    for x in range(5):
        elements.append(
            {
                "type": "way",
                "id": 200 + x,
                "nodes": [1 + y * 5 + x for y in range(5)],
                "tags": {"highway": "residential", "name": f"Synthetic street {x}"},
            }
        )
    for index, (x, y) in enumerate([(0, 0), (2, 0), (4, 1), (0, 4), (3, 3)]):
        lon, lat = bbox.west + (x + 1.1) * dx, bbox.south + (y + 1.1) * dy
        ids = []
        for corner, (ox, oy) in enumerate([(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)]):
            identifier = 1000 + index * 10 + corner
            ids.append(identifier)
            elements.append(
                {
                    "type": "node",
                    "id": identifier,
                    "lon": lon + ox * dx,
                    "lat": lat + oy * dy,
                    "tags": {"entrance": "main"} if corner == 0 else {},
                }
            )
        elements.append(
            {
                "type": "way",
                "id": 300 + index,
                "nodes": [*ids, ids[0]],
                "tags": {
                    "building": "apartments",
                    "building:levels": "5",
                    "name": f"Synthetic residence {index}",
                },
            }
        )
    for index, (x, y, tags) in enumerate(
        [
            (1, 1, {"office": "company"}),
            (3, 4, {"amenity": "school"}),
            (4, 4, {"shop": "supermarket"}),
            (2, 3, {"amenity": "cafe"}),
            (0, 2, {"leisure": "park"}),
            (4, 2, {"highway": "bus_stop"}),
        ]
    ):
        elements.append(
            {
                "type": "node",
                "id": 2000 + index,
                "lon": bbox.west + (x + 1.05) * dx,
                "lat": bbox.south + (y + 1.05) * dy,
                "tags": {
                    **tags,
                    "name": f"Synthetic destination {index}",
                    "opening_hours": "Mo-Su 07:00-23:00",
                },
            }
        )
    return {"version": 0.6, "generator": "UrbanFlow SYNTHETIC fixture v1", "elements": elements}
