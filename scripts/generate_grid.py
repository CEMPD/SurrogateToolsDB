"""
Generate modeling grid table(s) in PostGIS.

Reads grid definitions from a CSV config file, then for each grid:
  1. Checks that the target SRID exists in spatial_ref_sys
  2. Creates a table of grid cell polygons using PostGIS raster functions
  3. Builds a GIST spatial index

Usage:
    python scripts/generate_grid.py --grid-config grid_config.csv
                                    [--config database_config.csv]
"""

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

import db_utils

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config reader
# ---------------------------------------------------------------------------

def read_grid_config(csv_path: Path) -> list[dict]:
    """
    Read grid config CSV.

    Expected columns:
        name, schema, srid, xorig, yorig, xcellsize, ycellsize, cols, rows

    Returns:
        list of grid definition dicts
    """
    required = {"name", "schema", "srid", "xorig", "yorig",
                "xcellsize", "ycellsize", "cols", "rows"}
    grids = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(
            line for line in f
            if line.strip() and not line.strip().startswith("#")
        )
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            raise ValueError(
                f"{csv_path}: missing required columns: {missing}"
            )
        for row in reader:
            grids.append({
                "name":      row["name"].strip(),
                "schema":    row["schema"].strip(),
                "srid":      int(row["srid"].strip()),
                "xorig":     float(row["xorig"].strip()),
                "yorig":     float(row["yorig"].strip()),
                "xcellsize": float(row["xcellsize"].strip()),
                "ycellsize": float(row["ycellsize"].strip()),
                "cols":      int(row["cols"].strip()),
                "rows":      int(row["rows"].strip()),
            })
    return grids


# ---------------------------------------------------------------------------
# SRID check
# ---------------------------------------------------------------------------

def check_srid(con, srid: int) -> bool:
    """Return True if SRID exists in spatial_ref_sys."""
    result = con.raw_sql(
        f"SELECT COUNT(*) FROM spatial_ref_sys WHERE srid = {srid}"
    ).fetchone()
    return result[0] > 0


# ---------------------------------------------------------------------------
# Table existence check
# ---------------------------------------------------------------------------

def table_exists(con, table_name: str, schema: str = "public") -> bool:
    """Return True if table exists in the given schema."""
    result = con.raw_sql(f"""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = '{schema}'
          AND table_name   = '{table_name}'
    """).fetchone()
    return result[0] > 0


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def create_grid(con, grid: dict) -> dict:
    """
    Create a grid table using ST_MakeEmptyRaster + ST_PixelAsPolygons.

    Translates util/generate_modeling_grid.sh into Python.

    Args:
        con:  Ibis database connection
        grid: dict with keys: name, schema, srid, xorig, yorig,
              xcellsize, ycellsize, cols, rows

    Returns:
        status dict, e.g. {"name": ..., "status": "success", "rows": 229104}
    """
    name = grid["name"]
    schema = grid["schema"]
    srid = grid["srid"]
    start = time.time()

    # Check SRID
    if not check_srid(con, srid):
        logger.error("%s: SRID %d not found in spatial_ref_sys", name, srid)
        return {"name": name, "status": "error",
                "reason": f"SRID {srid} not found in spatial_ref_sys"}

    # Check if table already exists
    if table_exists(con, name, schema):
        logger.warning("%s: table already exists, dropping", name)
        con.raw_sql(f"DROP TABLE {schema}.{name}")

    # Create table
    con.raw_sql(f"""
        CREATE TABLE {schema}.{name} (
            colnum  INT NOT NULL,
            rownum  INT NOT NULL,
            gridcell geometry(Polygon, {srid}),
            PRIMARY KEY (colnum, rownum)
        )
    """)

    # Spatial index
    con.raw_sql(
        f"CREATE INDEX ON {schema}.{name} USING GIST (gridcell)"
    )

    # Generate grid cells
    con.raw_sql(f"""
        INSERT INTO {schema}.{name} (colnum, rownum, gridcell)
        SELECT (gv).x AS colnum, (gv).y AS rownum, (gv).geom
        FROM (
            SELECT ST_PixelAsPolygons(
                ST_AddBand(
                    ST_MakeEmptyRaster(
                        {grid['cols']}, {grid['rows']},
                        {grid['xorig']}, {grid['yorig']},
                        {grid['xcellsize']}, {grid['ycellsize']},
                        0, 0, {srid}
                    ),
                    '8BUI'::text, 1, 0
                )
            ) AS gv
        ) v
    """)

    # Row count
    cnt = con.raw_sql(
        f"SELECT COUNT(*) FROM {schema}.{name}"
    ).fetchone()[0]

    elapsed = round(time.time() - start, 1)
    logger.info("%s: created (%d rows, %.1fs)", name, cnt, elapsed)
    return {"name": name, "status": "success", "rows": cnt, "elapsed": elapsed}


# ---------------------------------------------------------------------------
# Process all grids
# ---------------------------------------------------------------------------

def create_all_grids(con, grids: list[dict]) -> list[dict]:
    """Create all grid tables from config."""
    results = []
    for grid in grids:
        try:
            result = create_grid(con, grid)
        except Exception as e:
            logger.error("%s: unexpected error: %s", grid["name"], e)
            result = {"name": grid["name"], "status": "error",
                      "reason": str(e)}
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]):
    """Print a summary of grid generation results."""
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]

    print(f"\n{'='*55}")
    print(f"Total: {len(results)}  "
          f"Success: {len(succeeded)}  "
          f"Failed: {len(failed)}")
    for r in succeeded:
        print(f"  {r['name']}: {r['rows']} rows ({r['elapsed']}s)")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['name']}: {r.get('reason', '')}")
    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate modeling grid table(s) in PostGIS."
    )
    parser.add_argument(
        "--grid-config", required=True,
        help="Path to grid config CSV")
    parser.add_argument(
        "--config", default=None,
        help="Path to database_config.csv")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Load DB config and connect
    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    # Read grid config
    grid_config_path = Path(args.grid_config)
    grids = read_grid_config(grid_config_path)
    logger.info("Grid config: %s (%d grids)", grid_config_path, len(grids))

    if not grids:
        logger.warning("No grids to generate")
        sys.exit(0)

    # Run
    results = create_all_grids(con, grids)
    print_summary(results)
    sys.exit(1 if any(r["status"] == "error" for r in results) else 0)


if __name__ == "__main__":
    main()
