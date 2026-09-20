import math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from shapely.geometry import shape

from urbanflow.common import Value
from urbanflow.simulation import Traversal
from urbanflow.world.ingest import projection
from urbanflow.world.models import World


class TrafficBin(Value):
    edge_id: str
    hour: datetime
    weekday: int
    weighted_count: float


def aggregate(traversals: list[Traversal], timezone: str) -> list[TrafficBin]:
    counts: dict[tuple[str, datetime], float] = defaultdict(float)
    for traversal in traversals:
        hour = traversal.started_at.astimezone(ZoneInfo(timezone)).replace(
            minute=0, second=0, microsecond=0
        )
        counts[traversal.edge_id, hour] += traversal.weight
    return [
        TrafficBin(edge_id=edge, hour=hour, weekday=hour.weekday(), weighted_count=count)
        for (edge, hour), count in sorted(counts.items())
    ]


def spatial_cells(world: World, counts: dict[str, float], size_m: int = 50) -> list[dict]:
    project = projection(world.bbox)
    cells: dict[tuple[int, int], float] = defaultdict(float)
    for edge in world.edges:
        center = shape(edge.geometry).interpolate(0.5, normalized=True)
        x, y = project.transform(center.x, center.y)
        cells[math.floor(x / size_m), math.floor(y / size_m)] += counts.get(edge.id, 0)
    result = []
    for (x, y), count in sorted(cells.items()):
        if count:
            lon, lat = project.transform(
                (x + 0.5) * size_m, (y + 0.5) * size_m, direction="INVERSE"
            )
            result.append({"position": [lon, lat], "weighted_count": count})
    return result
