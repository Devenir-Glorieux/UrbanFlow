import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


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
                "heatmap": [],
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
    assert all(method == "GET" for method, _ in calls)  # Rendering never starts an LLM run.
