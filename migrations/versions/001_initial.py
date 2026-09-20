"""Initial research schema; frozen snapshot in schema_v1.py."""

from alembic import op

from migrations.schema_v1 import metadata

revision = "001"
down_revision = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    metadata.create_all(op.get_bind())
    for kind in ("building", "poi", "entrance", "crossing", "transport_stop"):
        op.execute(f"CREATE VIEW {kind} AS SELECT * FROM city_object WHERE kind = '{kind}'")


def downgrade():
    for kind in ("building", "poi", "entrance", "crossing", "transport_stop"):
        op.execute(f"DROP VIEW {kind}")
    metadata.drop_all(op.get_bind())
