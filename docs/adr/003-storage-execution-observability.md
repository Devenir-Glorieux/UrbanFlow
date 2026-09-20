# ADR 003: PostGIS, in-process runs and container logs

Accepted 2026-09-19; updated 2026-09-20.

PostgreSQL/PostGIS is the experiment store; Alembic owns schema changes. Results
commit atomically, while validated LLM responses are cached independently so a
failed run can reuse them. Saved plans support replay without a live provider.

Small experiments run in the API/CLI process with bounded LLM concurrency. A job
queue is not needed at this stage. Use one API worker; restart recovery marks
unfinished runs failed rather than resuming them.

Operational JSON logs go to container output and are inspected with
`docker compose logs`. No separate collector or dashboard service is required.
Simulation results and replay depend on the database, not logs.

Compose is for local development: published ports bind to loopback and PostgreSQL
uses trust authentication. Remote deployment would require database and API
authentication.
