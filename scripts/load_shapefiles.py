"""
Load shapefiles into PostGIS using ogr2ogr, then validate geometries.

Usage:
    python scripts/load_shapefiles.py [--config PATH] [--load-list PATH]
                                      [--schema SCHEMA] [--dry-run]

Defaults:
    --config    ../database_config.csv  (relative to this script)
    --load-list ../shapefile_load.csv    (relative to config file or root)
    --schema    public
"""

import argparse
import csv
import logging
import subprocess
import sys
import time
from pathlib import Path

import db_utils

logger = logging.getLogger(__name__)


def read_load_list(csv_path: Path) -> list[dict]:
    """Read shapefile_load.csv. Skips blank lines and lines starting with #."""
    entries = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(
            line for line in f
            if line.strip() and not line.strip().startswith("#")
        )
        if not {"name", "dir"}.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"{csv_path}: missing required columns 'name' and/or 'dir'"
            )
        for row in reader:
            name = row["name"].strip()
            dir_path = Path(row["dir"].strip())
            if not dir_path.is_absolute():
                dir_path = csv_path.parent / dir_path
            entries.append({"name": name, "dir": dir_path})
    return entries


def build_pg_conn_str(pg: dict) -> str:
    """Build ogr2ogr PG connection string from the postgres section of the config."""
    user = pg.get("app_user") or pg.get("user")
    password = pg.get("app_password") or pg.get("password", "")
    parts = [
        f"dbname={pg['database']}",
        f"host={pg['host']}",
        f"port={pg['port']}",
        f"user={user}",
    ]
    if password:
        parts.append(f"password={password}")
    return "PG:" + " ".join(parts)


def build_ogr2ogr_cmd(pg_conn_str: str, shp_path: Path,
                      schema: str, table_name: str) -> list[str]:
    """
    Matches the original load_shapefile.2017.csh:
      ogr2ogr -f "PostgreSQL" "PG:..." input.shp
              -lco PRECISION=NO -nlt PROMOTE_TO_MULTI -nln schema.table -overwrite
    """
    return [
        "ogr2ogr",
        "-f", "PostgreSQL",
        pg_conn_str,
        str(shp_path),
        "-lco", "PRECISION=NO",
        "-nlt", "PROMOTE_TO_MULTI",
        "-nln", f"{schema}.{table_name}",
        "-overwrite",
    ]


def run_ogr2ogr(cmd: list[str], name: str, dry_run: bool = False) -> bool:
    """Run ogr2ogr via subprocess. In dry-run mode, print the command instead."""
    if dry_run:
        safe = ["***" if "password=" in p else p for p in cmd]
        logger.info("[dry-run] %s: %s", name, " ".join(safe))
        return True

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("%s: ogr2ogr failed\n%s", name, result.stderr.strip())
        return False

    logger.info("%s: loaded", name)
    return True


def apply_make_valid(con, schema: str, table_name: str):
    """
    Validete geometries on wkb_geometry.
    Replaces load_shapefile.2017.csh lines 26-28, but on wkb_geometry
    instead of reprojected geom like geom_900921 (reprojection is pushed back to the compute stage).
    """
    con.raw_sql(f"""
        UPDATE {schema}.{table_name}
        SET wkb_geometry = ST_MakeValid(wkb_geometry)
        WHERE NOT ST_IsValid(wkb_geometry)
    """)
    logger.info("%s: ST_MakeValid done", table_name)


def load_one(pg_conn_str: str, con, name: str, dir_path: Path,
             schema: str, dry_run: bool) -> dict:
    """Load a single shapefile: ogr2ogr → ST_MakeValid."""
    table_name = name.lower()
    shp_path = dir_path / f"{name}.shp"
    start = time.time()

    if not shp_path.exists():
        logger.error("%s: file not found: %s", name, shp_path)
        return {"name": name, "table_name": table_name,
                "status": "error", "error": f"file not found: {shp_path}"}

    cmd = build_ogr2ogr_cmd(pg_conn_str, shp_path, schema, table_name)
    if not run_ogr2ogr(cmd, name, dry_run):
        return {"name": name, "table_name": table_name,
                "status": "error", "error": "ogr2ogr failed"}

    if not dry_run:
        apply_make_valid(con, schema, table_name)

    return {"name": name, "table_name": table_name,
            "status": "success", "elapsed": round(time.time() - start, 1)}


def load_all(config: dict, load_list: list[dict],
             schema: str, dry_run: bool) -> list[dict]:
    """Load all shapefiles. On failure, log and continue to the next one."""
    pg_conn_str = build_pg_conn_str(config["database"]["postgres"])
    con = db_utils.connect_db(config)

    results = []
    for entry in load_list:
        try:
            result = load_one(pg_conn_str, con, entry["name"],
                              entry["dir"], schema, dry_run)
        except Exception as e:
            logger.error("%s: unexpected error: %s", entry["name"], e)
            result = {"name": entry["name"], "status": "error", "error": str(e)}
        results.append(result)

    return results


def print_summary(results: list[dict]):
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]
    print(f"\n{'='*50}")
    print(f"Total: {len(results)}  Succeeded: {len(succeeded)}  Failed: {len(failed)}")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['name']}: {r.get('error', '')}")
    print("="*50)


def main():
    parser = argparse.ArgumentParser(
        description="Load shapefiles into PostGIS via ogr2ogr."
    )
    parser.add_argument("--config", default=None,
                        help="Path to database_config.csv")
    parser.add_argument("--load-list", default=None,
                        help="Path to shapefile_load.csv")
    parser.add_argument("--schema", default="public",
                        help="Target PostgreSQL schema (default: public)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print ogr2ogr commands without executing them")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    config = db_utils.load_config(args.config)

    if args.load_list:
        load_list_path = Path(args.load_list)
    else:
        config_dir = (Path(args.config).parent if args.config
                      else Path(__file__).parent.parent)
        load_list_path = config_dir / "shapefile_load.csv"

    load_list = read_load_list(load_list_path)
    logger.info("load list: %s (%d shapefiles)", load_list_path, len(load_list))

    if not load_list:
        logger.warning("nothing to load")
        sys.exit(0)

    results = load_all(config, load_list, args.schema, args.dry_run)
    print_summary(results)
    sys.exit(1 if any(r["status"] == "error" for r in results) else 0)


if __name__ == "__main__":
    main()
