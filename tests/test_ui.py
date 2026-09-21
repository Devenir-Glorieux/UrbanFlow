import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.typing import PydeckState


@pytest.mark.parametrize(
    "page", ["World explorer", "Population", "Experiments", "Replay", "Validation"]
)
def test_ui_pages_render_with_persisted_api_values(monkeypatch, world, demo_config, page):
    features = [
        {
            "type": "Feature",
            "id": o.id,
            "geometry": o.geometry,
            "properties": {**o.model_dump(exclude={"geometry"}), "complete": True},
        }
        for o in world.objects
    ]
    features.extend(
        {
            "type": "Feature",
            "id": e.id,
            "geometry": e.geometry,
            "properties": {"id": e.id, "kind": "pedestrian_graph"},
        }
        for e in world.edges
    )
    run = {
        "id": "run",
        "world_id": world.id,
        "mode": "baseline",
        "model": "gravity-v1",
        "status": "completed",
        "config": demo_config["simulation"],
    }
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/worlds":
            data = [world.model_dump(mode="json", exclude={"objects", "nodes", "edges"})]
        elif request.url.path.endswith("/geojson"):
            data = {"type": "FeatureCollection", "features": features}
        elif request.url.path.endswith("/housing"):
            data = {
                "world_id": world.id,
                "building_statuses": {"residential": 5},
                "residential_buildings": 5,
                "routable_residential_buildings": 5,
                "homes_with_known_flats": 0,
                "minimum_agents": 5,
            }
        elif request.url.path == "/populations":
            data = [{"id": "pop", "world_id": world.id, "config": demo_config["population"]}]
        elif request.url.path == "/runs":
            data = [run]
        elif request.url.path == "/settings/llm":
            data = {"provider": "openai", "model": "test-model"}
        elif request.url.path.endswith("/traffic"):
            data = {
                "features": [
                    {**f, "properties": {"id": f["id"], "weighted_count": 0}}
                    for f in features
                    if f["properties"]["kind"] == "pedestrian_graph"
                ],
                "heatmap": [
                    {"position": [27.56, 53.90], "weighted_count": 12.5},
                    {"position": [27.561, 53.901], "weighted_count": 37.0},
                ],
                "total_segment_entries": 0,
                "hour": str(date(2026, 9, 14)),
            }
        else:
            data = []
        return httpx.Response(200, json=data)

    original_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    st.cache_data.clear()
    app = AppTest.from_file(Path("src/urbanflow/ui/app.py").resolve(), default_timeout=20).run()
    assert not app.exception
    if page != "World explorer":
        app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, json.dumps([e.message for e in app.exception])
    if page == "World explorer":
        assert all(widget.label != "Building colors" for widget in app.radio)
    if page == "Experiments":
        assert any(widget.label == "Saved 1 km² area" for widget in app.selectbox)
    if page == "Replay":
        assert any(widget.label == "Show agent trajectories" for widget in app.toggle)
        assert all("five" not in widget.label for widget in app.toggle)
        next(t for t in app.toggle if t.label == "Show sweaty spots").set_value(True).run()
        assert not app.exception
        chart = json.loads(app.get("deck_gl_json_chart")[0].proto.json)
        assert all(layer["@@type"] != "HeatmapLayer" for layer in chart["layers"])
        spots = next(layer for layer in chart["layers"] if layer["id"] == "sweaty-spots")
        assert spots["@@type"] == "GeoJsonLayer" and spots["pickable"]
        assert spots["pointRadiusUnits"] == "pixels"
        points = spots["data"]["features"]
        assert len({point["id"] for point in points}) == 2
        assert [point["properties"]["Pedestrian flow [people/hour]"] for point in points] == [
            12.5,
            37.0,
        ]
        assert all(point["properties"]["run_id"] == "run" for point in points)
        assert all(point["geometry"]["type"] == "Point" for point in points)
        for index, point in enumerate(points):
            app.session_state["replay-map"] = PydeckState(
                {
                    "selection": {
                        "indices": {"sweaty-spots": [index]},
                        "objects": {"sweaty-spots": [point]},
                    }
                }
            )
            app.run()
            assert not app.exception
            selected = json.loads(app.json[0].value)["sweaty-spots"][0]
            assert selected["id"] == point["id"]
            assert (
                selected["properties"]["Pedestrian flow [people/hour]"]
                == (point["properties"]["Pedestrian flow [people/hour]"])
            )
        next(t for t in app.toggle if t.label == "Show sweaty spots").set_value(False).run()
        next(t for t in app.toggle if t.label == "Show spatial intensity").set_value(True).run()
        assert not app.exception
        chart = json.loads(app.get("deck_gl_json_chart")[0].proto.json)
        assert all(layer["id"] != "sweaty-spots" for layer in chart["layers"])
        heat = next(layer for layer in chart["layers"] if layer["id"] == "heat")
        assert heat["@@type"] == "HeatmapLayer" and heat["radiusPixels"] == 35
        assert heat["data"] == [
            {"position": [27.56, 53.90], "weighted_count": 12.5},
            {"position": [27.561, 53.901], "weighted_count": 37.0},
        ]
    assert all(method == "GET" for method, _ in calls)  # Rendering never starts an LLM run.
