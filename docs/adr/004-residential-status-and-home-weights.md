# ADR 004: Residential coverage and agent weights

Accepted 2026-09-20.

Every eligible residential building must receive at least one synthetic agent.
Reject populations smaller than the number of homes. Represented population and
agent count are separate: each agent carries a weight.

Residential eligibility comes from OSM building type, current use and lifecycle
tags. Current `building:use` takes precedence over building type. Construction,
disused, abandoned and ruined buildings are excluded. Graph access is checked
separately; an unreachable home fails population generation.

Estimate relative housing capacity from `building:flats` when available. For other
homes, convert floor area to flat-equivalent capacity using the world's median
floor-area-per-flat. If no flat counts exist, use floor area. Distribute represented
population across homes in proportion to capacity, then across their agents.

These weights estimate relative capacity; they are not measured occupancy or
Minsk demographic statistics. `PopulationSource` provides a boundary for a future
measured-data implementation.
