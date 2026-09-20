"""Relational experiment schema. JSON is reserved for source/config/provider envelopes."""

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
world = Table(
    "world_dataset",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("source", String, nullable=False),
    Column("bbox", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("raw_snapshot", JSONB, nullable=False),
    Column("completeness", JSONB, nullable=False),
    Column("warnings", JSONB, nullable=False),
)
city_object = Table(
    "city_object",
    metadata,
    Column("world_id", ForeignKey("world_dataset.id"), primary_key=True),
    Column("id", String, primary_key=True),
    Column("osm_type", String, nullable=False),
    Column("osm_id", BigInteger, nullable=False),
    Column("kind", String, nullable=False),
    Column("geometry", Geometry("GEOMETRY", srid=4326), nullable=False),
    Column("lon", Float, nullable=False),
    Column("lat", Float, nullable=False),
    Column("tags", JSONB, nullable=False),
    Column("category", String),
    Column("levels", Float),
    Column("opening_hours", Text),
    Column("building_id", String),
    Column("node_id", String),
    ForeignKeyConstraint(["world_id", "building_id"], ["city_object.world_id", "city_object.id"]),
    CheckConstraint("kind IN ('building','poi','entrance','crossing','transport_stop')"),
)
node = Table(
    "pedestrian_node",
    metadata,
    Column("world_id", ForeignKey("world_dataset.id"), primary_key=True),
    Column("id", String, primary_key=True),
    Column("osm_id", BigInteger, nullable=False),
    Column("lon", Float, nullable=False),
    Column("lat", Float, nullable=False),
    Column("geometry", Geometry("POINT", srid=4326), nullable=False),
    Column("tags", JSONB, nullable=False),
)
edge = Table(
    "pedestrian_edge",
    metadata,
    Column("world_id", ForeignKey("world_dataset.id"), primary_key=True),
    Column("id", String, primary_key=True),
    Column("osm_id", BigInteger, nullable=False),
    Column("u", String, nullable=False),
    Column("v", String, nullable=False),
    Column("length_m", Float, nullable=False),
    Column("geometry", Geometry("LINESTRING", srid=4326), nullable=False),
    Column("tags", JSONB, nullable=False),
    Column("forward", Boolean, nullable=False),
    Column("backward", Boolean, nullable=False),
    ForeignKeyConstraint(["world_id", "u"], ["pedestrian_node.world_id", "pedestrian_node.id"]),
    ForeignKeyConstraint(["world_id", "v"], ["pedestrian_node.world_id", "pedestrian_node.id"]),
    CheckConstraint("length_m > 0"),
)
population = Table(
    "population_batch",
    metadata,
    Column("id", String, primary_key=True),
    Column("world_id", ForeignKey("world_dataset.id")),
    Column("config", JSONB, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("source", Text, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    UniqueConstraint("id", "world_id"),
)
archetype = Table(
    "population_archetype",
    metadata,
    Column("population_id", ForeignKey("population_batch.id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("fraction", Float, nullable=False),
    Column("min_age", Integer),
    Column("max_age", Integer),
    Column("role", String),
    Column("description", Text),
)
agent = Table(
    "synthetic_agent",
    metadata,
    Column("population_id", String, primary_key=True),
    Column("id", String, primary_key=True),
    Column("world_id", String, nullable=False),
    Column("archetype", String, nullable=False),
    Column("age", Integer, nullable=False),
    Column("role", String, nullable=False),
    Column("persona", Text, nullable=False),
    Column("weight", Float, nullable=False),
    Column("home_id", String, nullable=False),
    Column("anchor_id", String),
    ForeignKeyConstraint(
        ["population_id", "world_id"], ["population_batch.id", "population_batch.world_id"]
    ),
    ForeignKeyConstraint(
        ["population_id", "archetype"],
        ["population_archetype.population_id", "population_archetype.name"],
    ),
    ForeignKeyConstraint(["world_id", "home_id"], ["city_object.world_id", "city_object.id"]),
    ForeignKeyConstraint(["world_id", "anchor_id"], ["city_object.world_id", "city_object.id"]),
    CheckConstraint("weight > 0"),
)
run = Table(
    "simulation_run",
    metadata,
    Column("id", String, primary_key=True),
    Column("world_id", String, nullable=False),
    Column("population_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("status", String, nullable=False),
    Column("config", JSONB, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("provider", String, nullable=False),
    Column("model", String, nullable=False),
    Column("prompt_version", String, nullable=False),
    Column("code_version", String, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("error", Text),
    Column("replay_of", ForeignKey("simulation_run.id")),
    ForeignKeyConstraint(
        ["population_id", "world_id"], ["population_batch.id", "population_batch.world_id"]
    ),
    UniqueConstraint("id", "population_id"),
    UniqueConstraint("id", "world_id"),
    CheckConstraint("status IN ('running','completed','failed')"),
    CheckConstraint("mode IN ('baseline','llm')"),
)
plan = Table(
    "activity_plan",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("agent_id", String, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("population_id", String, nullable=False),
    ForeignKeyConstraint(
        ["run_id", "population_id"], ["simulation_run.id", "simulation_run.population_id"]
    ),
    ForeignKeyConstraint(
        ["population_id", "agent_id"], ["synthetic_agent.population_id", "synthetic_agent.id"]
    ),
)
activity = Table(
    "activity",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("agent_id", String, primary_key=True),
    Column("day", Date, primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("world_id", String, nullable=False),
    Column("destination_id", String, nullable=False),
    Column("minute", Integer, nullable=False),
    Column("purpose", String, nullable=False),
    ForeignKeyConstraint(
        ["run_id", "agent_id", "day"],
        ["activity_plan.run_id", "activity_plan.agent_id", "activity_plan.day"],
    ),
    ForeignKeyConstraint(
        ["world_id", "destination_id"], ["city_object.world_id", "city_object.id"]
    ),
    ForeignKeyConstraint(["run_id", "world_id"], ["simulation_run.id", "simulation_run.world_id"]),
    CheckConstraint("minute >= 0 AND minute < 1440"),
)
trip = Table(
    "trip",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("id", String, primary_key=True),
    Column("agent_id", String, nullable=False),
    Column("day", Date, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("origin_id", String, nullable=False),
    Column("destination_id", String, nullable=False),
    Column("purpose", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
    Column("distance_m", Float),
    ForeignKeyConstraint(
        ["run_id", "agent_id", "day"],
        ["activity_plan.run_id", "activity_plan.agent_id", "activity_plan.day"],
    ),
    CheckConstraint("ended_at >= started_at"),
)
traversal = Table(
    "edge_traversal",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("trip_id", String, primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("world_id", String, nullable=False),
    Column("agent_id", String, nullable=False),
    Column("edge_id", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
    Column("weight", Float),
    ForeignKeyConstraint(["run_id", "trip_id"], ["trip.run_id", "trip.id"]),
    ForeignKeyConstraint(
        ["world_id", "edge_id"], ["pedestrian_edge.world_id", "pedestrian_edge.id"]
    ),
    ForeignKeyConstraint(["run_id", "world_id"], ["simulation_run.id", "simulation_run.world_id"]),
    CheckConstraint("ended_at > started_at AND weight > 0"),
)
Index("ix_traversal_count", traversal.c.run_id, traversal.c.edge_id, traversal.c.started_at)
traffic = Table(
    "traffic_aggregation",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("edge_id", String, primary_key=True),
    Column("hour", DateTime(timezone=True), primary_key=True),
    Column("world_id", String),
    Column("weekday", Integer, nullable=False),
    Column("weighted_count", Float, nullable=False),
    ForeignKeyConstraint(
        ["world_id", "edge_id"], ["pedestrian_edge.world_id", "pedestrian_edge.id"]
    ),
    ForeignKeyConstraint(["run_id", "world_id"], ["simulation_run.id", "simulation_run.world_id"]),
)
cache = Table(
    "llm_response_cache",
    metadata,
    Column("key", String, primary_key=True),
    Column("request", JSONB, nullable=False),
    Column("response", JSONB, nullable=False),
    Column("call_metadata", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
observation = Table(
    "observation",
    metadata,
    Column("id", String, primary_key=True),
    Column("world_id", String, nullable=False),
    Column("edge_id", String, nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("count", Integer, nullable=False),
    Column("source", Text, nullable=False),
    Column("notes", Text, nullable=False),
    Column("geometry", Geometry("POINT", srid=4326), nullable=False),
    ForeignKeyConstraint(
        ["world_id", "edge_id"], ["pedestrian_edge.world_id", "pedestrian_edge.id"]
    ),
    CheckConstraint("ended_at > started_at AND count >= 0"),
)
validation_result = Table(
    "validation_result",
    metadata,
    Column("id", String, primary_key=True),
    Column("baseline_run_id", ForeignKey("simulation_run.id"), nullable=False),
    Column("llm_run_id", ForeignKey("simulation_run.id"), nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("baseline_mae", Float),
    Column("baseline_spearman", Float),
    Column("llm_mae", Float),
    Column("llm_spearman", Float),
    Column("n", Integer),
)
validation_pair = Table(
    "validation_pair",
    metadata,
    Column("result_id", ForeignKey("validation_result.id"), primary_key=True),
    Column("observation_id", ForeignKey("observation.id"), primary_key=True),
    Column("observed", Float),
    Column("baseline", Float),
    Column("llm", Float),
)
