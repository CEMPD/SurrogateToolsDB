"""
Database utilities - config loading, connection, and setup.

Public API:
  load_config()    - load YAML config
  connect_db()     - connect to target database via Ibis
  setup_database() - complete database setup (create DB, PostGIS, projections)
"""

from pathlib import Path

import ibis
import psycopg2
from psycopg2 import sql
import yaml


def load_config(config_path: str = None) -> dict:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to database_config.yaml (default: ../database_config.yaml relative to this script)

    Returns:
        dict with all configuration
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent / "database_config.yaml"
    else:
        config_path = Path(config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_db(config: dict, backend: str = None):
    """
    Connect to database using Ibis.

    Args:
        config: Config dict from load_config()
        backend: "postgres", "duckdb", or "sqlite" (default from config)

    Returns:
        Ibis connection object
    """
    backend = backend or config["database"]["backend"]

    if backend == "postgres":
        pg = config["database"]["postgres"]
        con = ibis.postgres.connect(
            host=pg["host"],
            port=pg["port"],
            database=pg["database"],
            user=pg["user"],
            password=pg.get("password", ""),
        )
        print(f"Connected to PostgreSQL: {pg['host']}:{pg['port']}/{pg['database']}")

    elif backend == "duckdb":
        db_path = config["database"]["duckdb"]["path"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        con = ibis.duckdb.connect(db_path)
        print(f"Connected to DuckDB: {db_path}")

    elif backend == "sqlite":
        db_path = config["database"]["sqlite"]["path"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        con = ibis.sqlite.connect(db_path)
        print(f"Connected to SQLite: {db_path}")

    else:
        raise ValueError(f"Unknown backend: {backend}")

    return con


# ============================================================================
# Database setup
# ============================================================================

def setup_database(config: dict):
    """
    Complete database setup. Automates README Steps 0a/0b/3:
      1. Create database if not exists
      2. Enable PostGIS extensions
      3. Register output modeling projections

    Returns:
        Ibis connection to the target database
    """
    pg = config["database"]["postgres"]

    # Step 1: Create database
    _create_database(pg)

    # Step 2: Connect to target database
    con = connect_db(config)

    # Step 3: Enable PostGIS + PostGIS raster
    _enable_postgis(con)

    # Step 4: Register projections
    _load_projections(con, config)

    return con


def _create_database(pg_config: dict):
    """Create target database if it doesn't exist."""
    dbname = pg_config["database"]

    conn = psycopg2.connect(
        host=pg_config["host"],
        port=pg_config["port"],
        database="postgres",
        user=pg_config["user"],
        password=pg_config.get("password", ""),
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone():
            print(f"Database '{dbname}' already exists")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            print(f"Created database '{dbname}'")
        cur.close()
    finally:
        conn.close()


def _enable_postgis(con):
    """Enable PostGIS and PostGIS raster extensions."""
    con.raw_sql("CREATE EXTENSION IF NOT EXISTS postgis")
    con.raw_sql("CREATE EXTENSION IF NOT EXISTS postgis_raster")
    print("PostGIS extensions enabled")


def _load_projections(con, config: dict):
    """Register output modeling projections from SQL files in config."""
    projections = config.get("spatial", {}).get("projections", {})
    if not projections:
        print("No projections configured")
        return

    for srid, sql_file in projections.items():
        srid = int(srid)

        result = con.raw_sql(
            f"SELECT COUNT(*) FROM spatial_ref_sys WHERE srid = {srid}"
        ).fetchone()
        if result[0] > 0:
            print(f"SRID {srid} already exists, skipping")
            continue

        path = Path(sql_file)
        if not path.exists():
            print(f"WARNING: projection SQL file not found: {path}")
            continue

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            print(f"WARNING: empty projection SQL file: {path}")
            continue

        con.raw_sql(raw)
        print(f"SRID {srid} loaded from {path.name}")


# ============================================================================
# Quick test
# ============================================================================

if __name__ == "__main__":
    config = load_config()
    con = setup_database(config)
    print(f"Setup complete. Tables: {con.list_tables()}")
