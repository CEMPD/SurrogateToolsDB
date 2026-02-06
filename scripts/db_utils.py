"""
Database utilities - config loading and Ibis connection.

Simple module with two main functions:
1. load_config() - load YAML config
2. connect_db() - connect to database via Ibis
"""

from pathlib import Path

import ibis
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
        # Default: database_config.yaml in parent directory of scripts/
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
# PostGIS spatial operations (raw SQL)
# ============================================================================

def setup_postgis(con):
    """Enable PostGIS extension."""
    con.raw_sql("CREATE EXTENSION IF NOT EXISTS postgis")
    print("PostGIS enabled")


def create_srid(con, srid: int, proj4: str, name: str = ""):
    """Create custom SRID if not exists."""
    # Check if exists
    result = con.raw_sql(f"SELECT COUNT(*) FROM spatial_ref_sys WHERE srid = {srid}").fetchone()
    if result[0] > 0:
        print(f"SRID {srid} already exists")
        return

    sql = f"""
        INSERT INTO spatial_ref_sys (srid, auth_name, auth_srid, srtext, proj4text)
        VALUES ({srid}, 'CUSTOM', {srid}, '{name}', '{proj4}')
    """
    con.raw_sql(sql)
    print(f"Created SRID {srid}")


# ============================================================================
# Quick test
# ============================================================================

if __name__ == "__main__":
    # Test config loading
    config = load_config()
    print(f"Database backend: {config['database']['backend']}")
    print(f"Target SRID: {config['spatial']['target_srid']}")
    print(f"Shapefiles: {list(config.get('shapefiles', {}).keys())}")

    # Test DuckDB connection (no server needed)
    con = connect_db(config, backend="duckdb")
    print(f"Tables: {con.list_tables()}")
