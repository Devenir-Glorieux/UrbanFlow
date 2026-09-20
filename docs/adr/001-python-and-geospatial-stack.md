# ADR 001: Python and geospatial pipeline

Accepted 2026-09-19.

Use CPython 3.14.2–3.14.x and uv. `pyproject.toml` declares compatibility constraints;
`uv.lock` records exact dependency versions. Verify Python 3.14 support before
changing dependencies.

The core importer uses asynchronous Overpass requests with an explicit OSM
transformer, Shapely for geometry, PyProj for metric projection and NetworkX for
routing. This keeps source IDs and graph topology under application control.
Access filtering and geometry handling therefore need their own tests.

OSMnx and GeoPandas remain in the optional `geo-research` group. The application
does not need their download or GeoDataFrame pipelines for its current workflow.

Streamlit uses PyDeck for world and traffic layers. Folium with streamlit-folium
provides map clicks for area selection; Python calculates the 1×1 km boundary.
The application image uses CPython on Debian. Compose explicitly selects the
amd64 PostGIS image, requiring emulation on ARM hosts.
