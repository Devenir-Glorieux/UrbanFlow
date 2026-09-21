import csv
import io
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import folium
import httpx
import pydeck as pdk
import streamlit as st
from streamlit_folium import st_folium

from urbanflow.ui.area_selector import bbox_feature, square_bbox
from urbanflow.world.models import BoundingBox

API = os.getenv("URBANFLOW_API_URL", "http://localhost:8000")
st.set_page_config(page_title="UrbanFlow", page_icon="🚶", layout="wide")
st.title("UrbanFlow")
st.caption("Pedestrian activity experiments · explore the city, replay movement, test predictions")


def api(path: str, body: Any = None) -> Any:
    try:
        with httpx.Client(timeout=3600) as client:
            response = (
                client.get(API + path) if body is None else client.post(API + path, json=body)
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        st.error(f"Request failed: {exc.response.text}")
        st.stop()
    except httpx.HTTPError:
        st.error("Cannot reach the API. Check that the Compose application is healthy.")
        st.stop()


@st.cache_data(ttl=60)
def world_features(world_id: str) -> dict:
    return api(f"/worlds/{world_id}/geojson")


@st.cache_data(ttl=60)
def housing_summary(world_id: str) -> dict:
    return api(f"/worlds/{world_id}/housing")


def draw(layers: list, bbox: dict, key: str) -> None:
    deck = pdk.Deck(
        layers=layers,
        map_style="light",
        initial_view_state=pdk.ViewState(
            longitude=(bbox["west"] + bbox["east"]) / 2,
            latitude=(bbox["south"] + bbox["north"]) / 2,
            zoom=15,
            pitch=0,
        ),
        tooltip={"text": "{id}"},  # type: ignore[arg-type] -- PyDeck accepts tooltip dictionaries.
    )
    event = st.pydeck_chart(
        deck, on_select="rerun", selection_mode="single-object", key=key, height=600
    )
    selection = event.selection  # type: ignore[union-attr]
    if selection and selection.get("objects"):
        with st.expander("Selected object attributes", expanded=True):
            st.json(selection["objects"])
    st.caption("Map data © OpenStreetMap contributors · ODbL. Basemap © CARTO.")


def geo_layer(identifier: str, features: list, color: list[int], **kwargs: Any) -> Any:
    return pdk.Layer(
        "GeoJsonLayer",
        id=identifier,
        data={"type": "FeatureCollection", "features": features},
        pickable=True,
        filled=True,
        stroked=True,
        get_fill_color=color,
        get_line_color=color,
        get_point_radius=5,
        line_width_min_pixels=2,
        **kwargs,
    )


def world_label(world: dict[str, Any]) -> str:
    bbox = world["bbox"]
    latitude = (bbox["south"] + bbox["north"]) / 2
    longitude = (bbox["west"] + bbox["east"]) / 2
    return f"{world['name']} · {latitude:.5f}, {longitude:.5f} · {world['id'][:8]}"


demo_config = json.loads(Path("configs/demo.json").read_text())
page = st.sidebar.radio(
    "Workspace", ["World explorer", "Population", "Experiments", "Replay", "Validation"]
)
if st.sidebar.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()
worlds = api("/worlds")
world = None
if worlds:
    selector_host = st if page == "Experiments" else st.sidebar
    world = selector_host.selectbox(
        "Saved 1 km² area",
        worlds,
        format_func=world_label,
        key="selected_world_area",
    )
    assert world is not None
    st.sidebar.caption(world["source"])
    if "SYNTHETIC" in world["source"]:
        st.warning(
            "Synthetic demonstration data. These are not actual Minsk buildings or pedestrian "
            "counts."
        )

if page == "World explorer":
    with st.expander("Import a real OpenStreetMap area", expanded=not worlds):
        initial_bbox = world["bbox"] if world is not None else demo_config["bbox"]
        initial_center = {
            "lat": (initial_bbox["south"] + initial_bbox["north"]) / 2,
            "lng": (initial_bbox["west"] + initial_bbox["east"]) / 2,
        }
        area_source = world["id"] if world is not None else "demo"
        if st.session_state.get("import_area_source") != area_source:
            st.session_state.import_area_center = initial_center
            st.session_state.import_area_source = area_source
        center = st.session_state.import_area_center
        selected_bbox = square_bbox(center["lat"], center["lng"])

        st.write("Move the 1×1 km square by clicking the desired location on the map.")
        selector = folium.Map(
            location=[center["lat"], center["lng"]],
            zoom_start=14,
            control_scale=True,
            tiles="OpenStreetMap",
        )
        folium.Rectangle(
            bounds=[
                [selected_bbox.south, selected_bbox.west],
                [selected_bbox.north, selected_bbox.east],
            ],
            color="#e4572e",
            weight=3,
            fill=True,
            fill_color="#e4572e",
            fill_opacity=0.16,
            tooltip="Selected area: 1.00 km²",
        ).add_to(selector)
        selection = st_folium(
            selector,
            key="import-area-map",
            height=480,
            use_container_width=True,
            returned_objects=["last_clicked"],
        )
        clicked = selection.get("last_clicked")
        if clicked and (
            abs(clicked["lat"] - center["lat"]) > 1e-7 or abs(clicked["lng"] - center["lng"]) > 1e-7
        ):
            st.session_state.import_area_center = {
                "lat": clicked["lat"],
                "lng": clicked["lng"],
            }
            st.rerun()

        st.caption(
            f"Center: {center['lat']:.6f}, {center['lng']:.6f} · Area: 1.00 km² · side: 1,000 m"
        )
        name = st.text_input("Area name", demo_config["name"])
        if st.button("Import selected 1 km²", type="primary"):
            with st.spinner("Downloading and transforming OpenStreetMap…"):
                api("/worlds", {"name": name, "bbox": selected_bbox.model_dump()})
            st.cache_data.clear()
            st.rerun()

if not worlds:
    st.info("Import an OSM area above, or run the offline demo command in README.md.")
    st.stop()

assert world is not None

if page == "World explorer":
    stats = world["completeness"]
    housing = housing_summary(world["id"])
    area_cols = st.columns(4)
    area_cols[0].metric("Buildings", stats["buildings"])
    area_cols[1].metric("Residential buildings", housing["residential_buildings"])
    area_cols[2].metric("POIs", stats["pois"])
    area_cols[3].metric("Pedestrian segments", stats["edges"])
    st.caption(
        f"{housing['routable_residential_buildings']} residential buildings are connected "
        f"to the pedestrian graph; flat counts are known for "
        f"{housing['homes_with_known_flats']}."
    )
    cols = st.columns(4)
    for col, label, key in zip(
        cols,
        ["Known building type", "Known levels", "With entrances", "Nearby POIs"],
        ["known_type_pct", "known_levels_pct", "with_entrances_pct", "with_nearby_pois_pct"],
        strict=False,
    ):
        col.metric(label, f"{stats[key]}%")
    options = ["building", "poi", "entrance", "pedestrian_graph", "crossing", "transport_stop"]
    selected = st.multiselect("Layers", options, default=["building", "poi", "pedestrian_graph"])
    colors = {
        "building": [95, 130, 170, 150],
        "poi": [240, 145, 30, 220],
        "entrance": [25, 185, 120, 240],
        "pedestrian_graph": [90, 100, 110, 180],
        "crossing": [220, 60, 100, 230],
        "transport_stop": [110, 75, 220, 230],
    }
    layers = []
    features = world_features(world["id"])["features"]
    for kind in selected:
        data = [f for f in features if f["properties"]["kind"] == kind]
        if kind == "building":
            for f in data:
                props = f["properties"]
                props["color"] = (
                    [80, 140, 210, 170]
                    if props["category"] in {"apartments", "residential", "house"}
                    else [180, 120, 170, 170]
                )
            layer = geo_layer(kind, data, colors[kind])
            layer.get_fill_color = "properties.color"
            layers.append(layer)
        else:
            layers.append(geo_layer(kind, data, colors[kind]))
    boundary = bbox_feature(BoundingBox.model_validate(world["bbox"]))
    layers.append(
        pdk.Layer(
            "GeoJsonLayer",
            id="world-boundary",
            data={"type": "FeatureCollection", "features": [boundary]},
            pickable=False,
            filled=False,
            stroked=True,
            get_line_color=[228, 87, 46, 255],
            line_width_min_pixels=4,
        )
    )
    draw(layers, world["bbox"], "world-map")
    st.caption(
        "Orange outline is the persisted import boundary. Blue buildings are residential; "
        "purple buildings are other or unknown types."
    )
    if world["warnings"]:
        with st.expander("Import warnings"):
            st.write(world["warnings"])

elif page == "Population":
    st.subheader("Generate a population")
    housing = housing_summary(world["id"])
    existing_populations = [
        population for population in api("/populations") if population["world_id"] == world["id"]
    ]
    columns = st.columns(3)
    columns[0].metric("Residential buildings", housing["residential_buildings"])
    columns[1].metric("Routable homes", housing["routable_residential_buildings"])
    columns[2].metric("Known flat counts", housing["homes_with_known_flats"])
    st.info(
        f"At least {housing['minimum_agents']} agents are required so every residential "
        "building has a representative. OSM has no resident counts; capacity uses "
        "building:flats where known and calibrated floor area elsewhere."
    )
    if housing["routable_residential_buildings"] != housing["residential_buildings"]:
        st.error(
            "Some residential buildings cannot reach the pedestrian graph within 150 m. "
            "Population generation will stop until the routing data is fixed."
        )
    template = json.loads(json.dumps(demo_config["population"]))
    if existing_populations:
        latest_population = max(
            existing_populations, key=lambda population: population.get("created_at") or ""
        )
        template = json.loads(json.dumps(latest_population["config"]))
    template["size"] = max(template["size"], housing["minimum_agents"])
    with st.form("population"):
        configuration = st.text_area(
            "Population configuration (JSON)",
            json.dumps(template, indent=2),
            height=400,
            key=f"population-config-{world['id']}",
        )
        submit = st.form_submit_button("Generate deterministic population")
    if submit:
        try:
            result = api(
                "/populations", {"world_id": world["id"], "config": json.loads(configuration)}
            )
            st.success(
                f"Generated {result['agents']} agents representing "
                f"{result['represented_population']:g} people across "
                f"{result['occupied_homes']} of {result['eligible_homes']} eligible homes; "
                f"at most {result['max_represented_population_in_home']:g} represented "
                "people in one home"
            )
            st.json(result)
        except json.JSONDecodeError:
            st.error("Enter valid JSON.")
    st.dataframe(existing_populations)

elif page == "Experiments":
    selected_bbox = world["bbox"]
    st.caption(
        "Populations and runs below belong to this saved area: "
        f"W {selected_bbox['west']:.6f}, S {selected_bbox['south']:.6f}, "
        f"E {selected_bbox['east']:.6f}, N {selected_bbox['north']:.6f}."
    )
    defaults = api("/settings/llm")
    housing = housing_summary(world["id"])
    populations = [
        population
        for population in api("/populations")
        if population["world_id"] == world["id"]
        and population["config"]["size"] >= housing["minimum_agents"]
    ]
    if not populations:
        st.info(
            "Generate a current population first. Older sparse populations that omit "
            "residential buildings cannot start a new experiment."
        )
        st.stop()
    with st.form("run"):
        pop = st.selectbox("Population", populations, format_func=lambda p: p["id"][:12])
        mode = st.selectbox("Behavior", ["baseline", "llm"])
        start = st.date_input(
            "Start date", date.fromisoformat(demo_config["simulation"]["start_date"])
        )
        days = st.number_input("Days", min_value=1, max_value=31, value=7)
        seed = st.number_input("Random seed", value=42)
        provider = st.selectbox(
            "LLM protocol (OpenRouter and local compatible servers use openai)",
            ["openai", "ollama"],
            index=1 if defaults["provider"] == "ollama" else 0,
        )
        model = st.text_input("Model name (from .env; can override per run)", defaults["model"])
        st.caption(
            "Provider endpoints and API keys are configured in .env. LLM runs call that provider."
        )
        with st.expander("LLM token budget"):
            tokens = st.number_input(
                "Initial output / reasoning tokens",
                min_value=256,
                max_value=131072,
                value=8192,
                step=256,
            )
            token_limit = st.number_input(
                "Maximum tokens after retries",
                min_value=256,
                max_value=131072,
                value=32768,
                step=256,
            )
            st.caption("Truncated responses retry with twice the token budget, up to this limit.")
        submit = st.form_submit_button("Run experiment")
    if submit and pop is not None:
        body = {
            "population_id": pop["id"],
            "mode": mode,
            "start_date": str(start),
            "days": days,
            "seed": seed,
        }
        if mode == "llm":
            if tokens > token_limit:
                st.error("Initial token budget must not exceed the maximum after retries.")
                st.stop()
            body["provider"] = {
                "provider": provider,
                "model": model,
                "max_completion_tokens": tokens,
                "max_completion_tokens_limit": token_limit,
            }
        with st.spinner("Running experiment and saving results…"):
            result = api("/runs", body)
        st.success(f"Completed experiment {result['id']}")
    runs = [r for r in api("/runs") if r["world_id"] == world["id"]]
    st.dataframe(
        [
            {k: r.get(k) for k in ("id", "mode", "status", "model", "created_at", "error")}
            for r in runs
        ]
    )

elif page == "Replay":
    runs = [r for r in api("/runs") if r["world_id"] == world["id"] and r["status"] == "completed"]
    if not runs:
        st.info("Complete an experiment first.")
        st.stop()
    run = st.selectbox(
        "Experiment", runs, format_func=lambda r: f"{r['mode']} · {r['model']} · {r['id'][:8]}"
    )
    assert run is not None
    start_day = date.fromisoformat(run["config"]["start_date"])
    day_index = st.select_slider(
        "Day",
        options=list(range(run["config"]["days"])),
        format_func=lambda i: (start_day + timedelta(days=i)).strftime("%A %d %b"),
    )
    initial_hour = st.slider("Hour", 0, 23, 8)
    playing = st.toggle("Play hourly timeline")
    heatmap = st.toggle("Show spatial intensity", value=False)
    show_spots = st.toggle("Show sweaty spots", value=False)
    show_agents = st.toggle("Show agent trajectories", value=False)
    if st.button("Re-execute saved plans (no LLM calls)"):
        with st.spinner("Replaying stored plans…"):
            replay = api(f"/runs/{run['id']}/replay", {})
        st.success(f"Saved replay {replay['id']}")
    state_key = f"{run['id']}:{day_index}:{initial_hour}"
    if st.session_state.get("timeline_key") != state_key:
        st.session_state.timeline_key = state_key
        st.session_state.timeline_hour = initial_hour

    @st.fragment(run_every=1.5 if playing else None)
    def timeline():
        assert run is not None
        assert world is not None
        hour = int(st.session_state.timeline_hour)
        day = start_day + timedelta(days=int(day_index))
        data = api(f"/runs/{run['id']}/traffic?day={day}&hour={hour}")
        st.write(f"{day:%A %d %B} · {hour:02}:00 · {run['config']['timezone']}")
        st.caption(f"{data['total_segment_entries']:g} weighted segment entries; not unique people")
        for feature in data["features"]:
            count = feature["properties"]["weighted_count"]
            feature["properties"]["color"] = (
                [230, max(30, 180 - min(150, count * 3)), 55, 220] if count else [130, 140, 150, 70]
            )
            feature["properties"]["width"] = 1 + min(14, count**0.5)
        layer = geo_layer("traffic", data["features"], [220, 100, 50, 230])
        layer.get_line_color = "properties.color"
        layer.get_line_width = "properties.width"
        layers = [layer]
        if heatmap:
            layers.append(
                pdk.Layer(
                    "HeatmapLayer",
                    id="heat",
                    data=data["heatmap"],
                    get_position="position",
                    get_weight="weighted_count",
                    radius_pixels=35,
                )
            )
        if show_agents:
            trajectories = api(f"/runs/{run['id']}/trajectories?day={day}&hour={hour}")
            layers.append(geo_layer("agents", trajectories["features"], [60, 90, 240, 230]))
        if show_spots:
            spots = []
            for cell in data["heatmap"]:
                lon, lat = cell["position"]
                identifier = f"cell:{lon:.8f}:{lat:.8f}"
                spots.append(
                    {
                        "type": "Feature",
                        "id": identifier,
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "id": identifier,
                            "Pedestrian flow [people/hour]": cell["weighted_count"],
                            "hour": data["hour"],
                            "timezone": run["config"]["timezone"],
                            "cell_size_m": 50,
                            "run_id": run["id"],
                        },
                    }
                )
            spots_layer = geo_layer(
                "sweaty-spots", spots, [240, 145, 30, 240], point_radius_units='"pixels"'
            )
            spots_layer.get_point_radius = 6
            layers.append(spots_layer)
            st.caption(
                "Click a spot to inspect its attributes. Each point represents a 50 m cell; "
                "flow sums weighted segment entries in that cell, not unique people."
            )
        draw(layers, world["bbox"], "replay-map")
        if playing:
            st.session_state.timeline_hour = (hour + 1) % 24

    timeline()

elif page == "Validation":
    st.subheader("Manual pedestrian counts")
    st.caption(
        "Count both directions at one segment. Times use Europe/Minsk; "
        "intervals include start and exclude end."
    )
    edges = [
        f
        for f in world_features(world["id"])["features"]
        if f["properties"]["kind"] == "pedestrian_graph"
    ]
    with st.form("observation"):
        edge = st.selectbox("Street segment", edges, format_func=lambda f: f["id"])
        obs_day = st.date_input("Date", date(2026, 9, 14))
        obs_time = st.time_input("Start time", time(8))
        minutes = st.number_input(
            "Counting interval (minutes)", min_value=1, max_value=1440, value=60
        )
        count = st.number_input("Observed pedestrian count", min_value=0, value=0)
        source = st.text_input("Observer / source", "")
        notes = st.text_input("Notes", "")
        submit = st.form_submit_button("Save observation")
    if submit and edge is not None:
        started = datetime.combine(obs_day, obs_time, ZoneInfo("Europe/Minsk"))
        api(
            "/observations",
            [
                {
                    "world_id": world["id"],
                    "edge_id": edge["id"],
                    "started_at": started.isoformat(),
                    "ended_at": (started + timedelta(minutes=minutes)).isoformat(),
                    "count": count,
                    "source": source,
                    "notes": notes,
                }
            ],
        )
        st.success("Observation saved")
    uploaded = st.file_uploader("Import observation CSV", type=["csv"])
    st.caption(
        "Columns: edge_id, started_at, ended_at, count, source, notes. "
        "Use ISO timestamps with offsets."
    )
    if uploaded is not None and st.button("Import CSV"):
        rows = list(csv.DictReader(io.StringIO(uploaded.getvalue().decode("utf-8-sig"))))
        api("/observations", [{**row, "world_id": world["id"]} for row in rows])
        st.success(f"Imported {len(rows)} observations")
    st.dataframe(api(f"/observations?world_id={world['id']}"))
    runs = [r for r in api("/runs") if r["world_id"] == world["id"] and r["status"] == "completed"]
    baselines, llms = (
        [r for r in runs if r["mode"] == "baseline"],
        [r for r in runs if r["mode"] == "llm"],
    )
    if baselines and llms:
        baseline = st.selectbox("Baseline", baselines, format_func=lambda r: r["id"])
        llm = st.selectbox(
            "LLM experiment", llms, format_func=lambda r: f"{r['model']} · {r['id']}"
        )
        if st.button("Compare against observations") and baseline and llm:
            result = api(
                "/validation", {"baseline_run_id": baseline["id"], "llm_run_id": llm["id"]}
            )
            st.dataframe([{"model": key, **result[key]} for key in ("baseline", "llm")])
            st.dataframe(result["pairs"])
            st.caption(result["note"])
    else:
        st.info(
            "Complete baseline and LLM runs on the same population and interval to compare them."
        )
