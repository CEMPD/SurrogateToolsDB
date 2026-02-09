"""
Database utilities - config loading, connection, and setup.

Main functions:
  load_config()    - load YAML config
  connect_db()     - connect to target database via Ibis
  setup_database() - complete database setup (create DB, PostGIS, projections)
"""

import sys
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
        username = pg.get("app_user") or pg.get("user")
        password = pg.get("app_password") or pg.get("password", "")
        con = ibis.postgres.connect(
            host=pg["host"],
            port=pg["port"],
            database=pg["database"],
            user=username,
            password=password,
        )
        print(f"Connected to PostgreSQL: {pg['host']}:{pg['port']}/{pg['database']} as {username}")

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

def setup_database(config: dict, config_path: str = None):
    """
    Complete database setup with db user detection.

    Config field names determine the mode:
      - `user`/`password`         → superuser mode (first run)
      - `app_user`/`app_password` → app user mode (subsequent runs)

    In superuser mode: creates DB, extensions, projections, prompts for app
    credentials, creates the app user, grants privileges, and rewrites config.

    In app user mode: connects directly, enables extensions,
    loads projections (skips existing).

    Args:
        config: Config dict from load_config()
        config_path: Path to database_config.yaml (needed for config rewrite)

    Returns:
        Ibis connection to the target database
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent / "database_config.yaml"
    else:
        config_path = Path(config_path)

    pg = config["database"]["postgres"]

    if pg.get("app_user"):
        # --- App user mode ---
        return _setup_app_user_mode(config, pg)
    elif pg.get("user"):
        # --- Super user mode ---
        return _setup_super_user_mode(config, pg, config_path)
    else:
        print("ERROR: Config must contain either 'user' (superuser) or 'app_user' (app user)")
        sys.exit(1)


def _setup_app_user_mode(config, pg):
    """App user: DB must already exist."""
    dbname = pg["database"]

    # Check that the database exists
    try:
        conn = psycopg2.connect(
            host=pg["host"],
            port=pg["port"],
            database=dbname,
            user=pg["app_user"],
            password=pg.get("app_password", ""),
        )
        conn.close()
    except psycopg2.OperationalError:
        print(f"ERROR: Database '{dbname}' does not exist or app user cannot connect.")
        print("Run setup with superuser credentials first.")
        sys.exit(1)

    con = connect_db(config)
    _enable_postgis(con)
    _load_projections(con, config)
    return con


def _setup_super_user_mode(config, pg, config_path):
    """Super user: full first-run setup."""
    # Step 1: Create database
    _create_database(pg)

    # Step 2: Connect to target database as superuser
    con = connect_db(config)

    # Step 3: Enable PostGIS + PostGIS raster
    _enable_postgis(con)

    # Step 4: Register projections
    _load_projections(con, config)

    # Step 5: Prompt for app user credentials
    app_user, app_password = _prompt_app_user()

    # Step 6: Create app user & grant privileges
    _create_user(pg, app_user, app_password)

    # Step 7: Rewrite config to app user mode
    _rewrite_config(config_path, app_user, app_password)

    # Step 8: Update in-memory config and reconnect as app user
    config["database"]["postgres"].pop("user", None)
    config["database"]["postgres"].pop("password", None)
    config["database"]["postgres"]["app_user"] = app_user
    config["database"]["postgres"]["app_password"] = app_password

    con = connect_db(config)
    return con


def _prompt_app_user():
    """Prompt for app username and password."""
    app_user = input("Enter app username [Default:pgsurg]: ").strip() or "pgsurg"
    app_password = input("Enter app user password [Default: 1234]: ").strip() or "1234"
    return app_user, app_password


def _rewrite_config(config_path, app_user, app_password):
    """Rewrite config file: replace user/password with app_user/app_password."""
    config_path = Path(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    pg = cfg["database"]["postgres"]
    pg.pop("user", None)
    pg.pop("password", None)
    pg["app_user"] = app_user
    pg["app_password"] = app_password

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False)

    print(f"Config updated: now using app_user '{app_user}'")


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


def _create_user(pg_config: dict, app_user: str, app_password: str):
    """Create app user and grant privileges on the target database."""
    dbname = pg_config["database"]

    conn = psycopg2.connect(
        host=pg_config["host"],
        port=pg_config["port"],
        database=dbname,
        user=pg_config["user"],
        password=pg_config.get("password", ""),
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()

        # Create user if not exists
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,))
        if cur.fetchone():
            print(f"User '{app_user}' already exists")
        else:
            cur.execute(
                sql.SQL("CREATE USER {} WITH PASSWORD %s").format(
                    sql.Identifier(app_user)
                ),
                (app_password,),
            )
            print(f"Created user '{app_user}'")

        # Grant privileges
        cur.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                sql.Identifier(dbname), sql.Identifier(app_user)
            )
        )
        cur.execute(
            sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(app_user)
            )
        )
        cur.execute(
            sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(app_user)
            )
        )
        print(f"Granted privileges to '{app_user}' on '{dbname}'")

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
    tables = con.raw_sql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    ).fetchall()
    views = con.raw_sql(
        "SELECT viewname FROM pg_views WHERE schemaname = 'public'"
    ).fetchall()
    print(f"Setup complete.")
    print(f"  Tables: {[t[0] for t in tables]}")
    print(f"  Views:  {[v[0] for v in views]}")
