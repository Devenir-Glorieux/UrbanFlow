import copy

import httpx
import pytest
from shapely.geometry import shape

from urbanflow.common import DomainError
from urbanflow.world.ingest import fetch_osm, pedestrian_access, transform_osm


def test_transform_preserves_source_and_completeness(world):
    assert world.completeness["buildings"] == 5
    assert world.completeness["known_type_pct"] == 100
    assert world.completeness["known_levels_pct"] == 100
    assert world.completeness["with_entrances_pct"] == 100
    assert len(world.edges) == 40
    assert {o.kind for o in world.objects} == {
        "building",
        "poi",
        "entrance",
        "crossing",
        "transport_stop",
    }
    cafe = next(o for o in world.objects if o.category == "amenity:cafe")
    assert cafe.opening_hours == "Mo-Su 07:00-23:00"
    assert cafe.osm_id == 2003
    assert all(100 < edge.length_m < 200 for edge in world.edges)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "motorway"}, False),
        ({"highway": "footway", "foot": "no"}, False),
        ({"highway": "residential", "access": "private"}, False),
        ({"highway": "footway", "access": "private", "foot": "yes"}, True),
        ({"highway": "residential", "oneway": "yes"}, True),
        ({"highway": "cycleway"}, False),
        ({"highway": "cycleway", "foot": "yes"}, True),
        ({"highway": "pedestrian", "area": "yes"}, False),
    ],
)
def test_access(tags, expected):
    assert pedestrian_access(tags) == expected


def test_missing_node_does_not_bridge_way(raw, bbox):
    raw["elements"] = [e for e in raw["elements"] if not (e["type"] == "node" and e["id"] == 3)]
    world = transform_osm(raw, bbox, "test", "test")
    assert not any(e.osm_id in {100, 202} for e in world.edges)


def test_multipolygon_building_is_not_double_counted(raw, bbox):
    building = next(e for e in raw["elements"] if e["type"] == "way" and e["id"] == 300)
    raw["elements"].append(
        {
            "type": "relation",
            "id": 999,
            "members": [{"type": "way", "ref": 300, "role": "outer"}],
            "tags": {**building["tags"], "type": "multipolygon"},
        }
    )
    result = transform_osm(raw, bbox, "test", "test")
    assert result.completeness["buildings"] == 5
    assert any(o.id == "building:relation/999" for o in result.objects)
    assert all(shape(o.geometry).is_valid for o in result.objects)


def test_hash_is_stable_and_source_sensitive(raw, bbox):
    first = transform_osm(raw, bbox, "one", "test")
    second = transform_osm(copy.deepcopy(raw), bbox, "two", "test")
    assert first.id == second.id
    raw["elements"][0]["tags"]["name"] = "changed"
    assert transform_osm(raw, bbox, "one", "test").id != first.id


def test_empty_world_rejected(bbox):
    with pytest.raises(DomainError, match="no usable pedestrian edges"):
        transform_osm({"elements": []}, bbox, "test", "test")


async def test_overpass_headers_and_incomplete_response(bbox, monkeypatch):
    original = httpx.AsyncClient

    def respond(request):
        assert request.headers["accept"] == "application/json"
        assert request.headers["user-agent"].startswith("UrbanFlow/")
        return httpx.Response(200, json={"remark": "runtime error: timeout", "elements": []})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(DomainError, match="incomplete"):
        await fetch_osm(bbox, "https://overpass.test/interpreter")
