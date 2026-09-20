"""Physical movement is deterministic and independent of behavior providers."""

import networkx as nx
from shapely.geometry import Point

from urbanflow.common import DomainError
from urbanflow.world.ingest import projection
from urbanflow.world.models import CityObject, PedestrianEdge, World


class Router:
    def __init__(self, world: World, max_snap_m: float = 150):
        self.graph = nx.DiGraph()
        self.edges = {e.id: e for e in world.edges}
        self.project = projection(world.bbox)
        self.max_snap_m = max_snap_m
        self.positions = {n.id: Point(*self.project.transform(n.lon, n.lat)) for n in world.nodes}
        for node in sorted(self.positions):
            self.graph.add_node(node)
        for edge in sorted(world.edges, key=lambda e: e.id):
            for u, v, allowed in ((edge.u, edge.v, edge.forward), (edge.v, edge.u, edge.backward)):
                if not allowed:
                    continue
                existing = self.graph.get_edge_data(u, v)
                if existing is None or (edge.length_m, edge.id) < (
                    existing["weight"],
                    existing["edge_id"],
                ):
                    self.graph.add_edge(u, v, weight=edge.length_m, edge_id=edge.id)
        self.components = {
            node: i
            for i, component in enumerate(nx.strongly_connected_components(self.graph))
            for node in component
        }
        self.objects = {o.id: o for o in world.objects}
        self._routes: dict[tuple[str, str], tuple[str, ...]] = {}
        self._distances: dict[tuple[str, str], float] = {}
        self.snaps: dict[str, tuple[str, float]] = {}
        for obj in world.objects:
            point = Point(*self.project.transform(obj.lon, obj.lat))
            distance, node = min((point.distance(p), n) for n, p in self.positions.items())
            if distance <= max_snap_m:
                self.snaps[obj.id] = (node, distance)

    def connected(self, origin: str, destination: str) -> bool:
        if origin not in self.snaps or destination not in self.snaps:
            return False
        return self.components[self.snaps[origin][0]] == self.components[self.snaps[destination][0]]

    def route(self, origin: str, destination: str) -> list[PedestrianEdge]:
        if not self.connected(origin, destination):
            raise DomainError(f"No pedestrian round-trip connection: {origin} → {destination}")
        key = (origin, destination)
        cached = self._routes.get(key)
        if cached is not None:
            return [self.edges[edge_id] for edge_id in cached]
        nodes = nx.shortest_path(
            self.graph, self.snaps[origin][0], self.snaps[destination][0], weight="weight"
        )
        edge_ids = tuple(
            self.graph[u][v]["edge_id"] for u, v in zip(nodes, nodes[1:], strict=False)
        )
        self._routes[key] = edge_ids
        return [self.edges[edge_id] for edge_id in edge_ids]

    def distance(self, origin: str, destination: str) -> float:
        if origin == destination:
            return 0
        key = (origin, destination)
        cached = self._distances.get(key)
        if cached is not None:
            return cached
        distance = (
            sum(e.length_m for e in self.route(origin, destination))
            + self.snaps[origin][1]
            + self.snaps[destination][1]
        )
        self._distances[key] = distance
        return distance

    def candidates(self, home: str) -> list[CityObject]:
        return [
            o
            for o in sorted(self.objects.values(), key=lambda o: o.id)
            if (o.id == home or o.kind == "poi") and self.connected(home, o.id)
        ]
