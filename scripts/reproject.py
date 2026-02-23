"""
Reproject shapefile tables to a target SRID and compute density columns.

This module sits between shapefile loading (load_shapefiles.py) and surrogate
computation (future compute_surrogate.py).  It reads geometry type and density
attribute metadata from the extended shapefile catalog CSV, then for each table:

  1. Ensures the target SRID is registered in spatial_ref_sys
  2. Adds a reprojected geometry column  (geom_{srid})
  3. Creates a GIST spatial index
  4. Validates reprojected geometry      (ST_MakeValid)
  5. Computes area/length columns
  6. Computes density columns            ({attr}_dens_{srid})

Columns that already exist are skipped.

Usage:
    python scripts/reproject.py --srid 900921 --catalog shapefile_catalog_pg.2017.csv
                                [--sql-file util/create_900921.sql]
                                [--tables table1 table2 ...]
                                [--config database_config.csv] [--schema public]
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
# Catalog reader
# ---------------------------------------------------------------------------

def read_shapefile_catalog(csv_path: Path) -> dict:
    """
    Read shapefile catalog CSV.

    Expected columns (case-sensitive headers):
        SHAPEFILE NAME, DIRECTORY, ELLIPSOID, PROJECTION,
        GEOMTYPE, DENSITY_ATTRS, DESCRIPTION, DATA SOURCE, QUESTION

    Returns:
        dict keyed by lowercase table name, e.g.
        {
          "acs2016_5yr_bg": {
              "geomtype":      "MultiPolygon",
              "density_attrs": ["pop2016", "hu2016"],
              "ellipsoid":     "datum=NAD83",
              "projection":    "proj=latlong",
          }, ...
        }
    """
    catalog = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"SHAPEFILE NAME"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"{csv_path}: missing required column 'SHAPEFILE NAME'"
            )
        for row in reader:
            name = row["SHAPEFILE NAME"].strip()
            if not name:
                continue
            table = name.lower()

            # Parse density attrs (comma-separated, may be empty)
            raw_attrs = row.get("DENSITY_ATTRS", "").strip()
            density_attrs = (
                [a.strip() for a in raw_attrs.split(",") if a.strip()]
                if raw_attrs else []
            )

            catalog[table] = {
                "geomtype":      row.get("GEOMTYPE", "").strip(),
                "density_attrs": density_attrs,
                "ellipsoid":     row.get("ELLIPSOID", "").strip(),
                "projection":    row.get("PROJECTION", "").strip(),
            }
    return catalog


# ---------------------------------------------------------------------------
# SRID helpers
# ---------------------------------------------------------------------------

def ensure_srid(con, srid: int, sql_file: str = None) -> bool:
    """
    Make sure *srid* is registered in ``spatial_ref_sys``.

    If the SRID already exists, return True immediately.
    If it does not exist and *sql_file* is provided, execute the SQL file
    to register it, then return True.
    If it does not exist and no *sql_file* is given, return False.

    From README: psql -h $server -U $user $dbname -f util/create_900921.sql
    """
    result = con.raw_sql(
        f"SELECT COUNT(*) FROM spatial_ref_sys WHERE srid = {srid}"
    ).fetchone()
    if result[0] > 0:
        logger.info("SRID %d already registered", srid)
        return True

    if sql_file is None:
        logger.error("SRID %d not found and no SQL file provided", srid)
        return False

    path = Path(sql_file)
    if not path.exists():
        logger.error("Projection SQL file not found: %s", path)
        return False

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        logger.error("Empty projection SQL file: %s", path)
        return False

    con.raw_sql(raw)
    logger.info("SRID %d loaded from %s", srid, path.name)
    return True


# ---------------------------------------------------------------------------
# Check if column exists
# ---------------------------------------------------------------------------

def column_exists(con, table_name: str, column_name: str,
                  schema: str = "public") -> bool:
    """Return True if *column_name* already exists in *schema.table_name*."""
    result = con.raw_sql(f"""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = '{schema}'
          AND table_name   = '{table_name}'
          AND column_name  = '{column_name}'
    """).fetchone()
    return result[0] > 0


# ---------------------------------------------------------------------------
# Reprojection
# ---------------------------------------------------------------------------

def reproject_table(con, table_name: str, srid: int, geomtype: str,
                    schema: str = "public") -> dict:
    """
    Add ``geom_{srid}`` column, transform, index, and validate.

    Translates the legacy SQL from util/load_shapefile.2017.csh

    Returns a status dict, e.g. {"table": ..., "status": "success"}.
    """
    col_name = f"geom_{srid}"
    start = time.time()

    # Skip if already done
    if column_exists(con, table_name, col_name, schema):
        logger.info("%s: %s already exists, skipping reprojection",
                    table_name, col_name)
        return {"table": table_name, "status": "skipped",
                "reason": "already exists"}

    if not geomtype:
        logger.error("%s: no geomtype specified, cannot reproject", table_name)
        return {"table": table_name, "status": "error",
                "reason": "no geomtype"}

    # Check if wkb_geometry already uses the target SRID
    src_srid = con.raw_sql(f"""
        SELECT srid FROM geometry_columns
        WHERE f_table_schema = '{schema}'
          AND f_table_name   = '{table_name}'
          AND f_geometry_column = 'wkb_geometry'
    """).fetchone()
    if src_srid and src_srid[0] == srid:
        logger.warning(
            "%s: wkb_geometry already in SRID %d — copying without transform",
            table_name, srid)

    # Step 1 — add typed geometry column
    con.raw_sql(f"""
        ALTER TABLE {schema}.{table_name}
        ADD COLUMN {col_name} geometry({geomtype}, {srid})
    """)

    # Step 2 — transform or copy
    already_matches = src_srid and src_srid[0] == srid
    if already_matches:
        # Source already in target SRID — copy geometry, skip transform
        con.raw_sql(f"""
            UPDATE {schema}.{table_name}
            SET {col_name} = ST_Multi(wkb_geometry)
        """)
    else:
        con.raw_sql(f"""
            UPDATE {schema}.{table_name}
            SET {col_name} = ST_Multi(ST_Transform(wkb_geometry, {srid}))
        """)

    # Step 3 — spatial index
    con.raw_sql(
        f"DROP INDEX IF EXISTS {schema}.{table_name}_wkb_geometry_geom_idx"
    )
    con.raw_sql(
        f"CREATE INDEX ON {schema}.{table_name} USING GIST({col_name})"
    )

    # Step 4 — validate reprojected geometry
    con.raw_sql(f"""
        UPDATE {schema}.{table_name}
        SET {col_name} = ST_MakeValid({col_name})
        WHERE NOT ST_IsValid({col_name})
    """)

    elapsed = round(time.time() - start, 1)
    logger.info("%s: reprojected to SRID %d (%.1fs)", table_name, srid, elapsed)
    return {"table": table_name, "status": "success",
            "srid": srid, "elapsed": elapsed}


# ---------------------------------------------------------------------------
# Area / Length / Density
# ---------------------------------------------------------------------------

def compute_area(con, table_name: str, srid: int,
                 schema: str = "public"):
    """Add ``area_{srid}`` column for polygon tables."""
    col = f"area_{srid}"
    if column_exists(con, table_name, col, schema):
        logger.info("%s: %s already exists", table_name, col)
        return
    con.raw_sql(f"""
        ALTER TABLE {schema}.{table_name}
        ADD COLUMN {col} double precision
    """)
    con.raw_sql(f"""
        UPDATE {schema}.{table_name}
        SET {col} = ST_Area(geom_{srid})
    """)
    logger.info("%s: %s computed", table_name, col)


def compute_length(con, table_name: str, srid: int,
                   schema: str = "public"):
    """Add ``length_{srid}`` column for line tables."""
    col = f"length_{srid}"
    if column_exists(con, table_name, col, schema):
        logger.info("%s: %s already exists", table_name, col)
        return
    con.raw_sql(f"""
        ALTER TABLE {schema}.{table_name}
        ADD COLUMN {col} double precision
    """)
    con.raw_sql(f"""
        UPDATE {schema}.{table_name}
        SET {col} = ST_Length(geom_{srid})
    """)
    logger.info("%s: %s computed", table_name, col)


def compute_density(con, table_name: str, srid: int, schema: str,
                    weight_attr: str, measure: str):
    """
    Add ``{weight_attr}_dens_{srid}`` column.

    density = weight_attr / measure_{srid}

    *measure* is ``"area"`` for polygons or ``"length"`` for lines.
    NULLIF to avoid division-by-zero.
    """
    dens_col = f"{weight_attr}_dens_{srid}"
    if column_exists(con, table_name, dens_col, schema):
        logger.info("%s: %s already exists", table_name, dens_col)
        return
    measure_col = f"{measure}_{srid}"
    con.raw_sql(f"""
        ALTER TABLE {schema}.{table_name}
        ADD COLUMN {dens_col} double precision
    """)
    con.raw_sql(f"""
        UPDATE {schema}.{table_name}
        SET {dens_col} = {weight_attr} / NULLIF({measure_col}, 0)
    """)
    logger.info("%s: %s computed", table_name, dens_col)


def compute_densities_for_table(con, table_name: str, srid: int,
                                schema: str, density_attrs: list[str],
                                geomtype: str):
    """
    Compute area/length then density columns, based on geometry type.

    - Polygon  → area + density
    - Line     → length + density
    - Point    → skip (count-based, no density)
    """
    gt = geomtype.upper()

    if "POLYGON" in gt:
        compute_area(con, table_name, srid, schema)
        measure = "area"
    elif "LINE" in gt:
        compute_length(con, table_name, srid, schema)
        measure = "length"
    else:
        logger.info("%s: %s geometry, no density columns needed",
                    table_name, geomtype)
        return

    for attr in density_attrs:
        compute_density(con, table_name, srid, schema, attr, measure)


# ---------------------------------------------------------------------------
# Process all tables
# ---------------------------------------------------------------------------

def reproject_tables(con, catalog: dict, table_names: list[str],
                     srid: int, schema: str,
                     sql_file: str = None) -> list[dict]:
    """
    Reproject and compute densities for a list of tables.

    Args:
        con:          Ibis database connection
        catalog:      dict from read_shapefile_catalog()
        table_names:  tables to process
        srid:         target SRID
        schema:       PostgreSQL schema
        sql_file:     optional path to SQL file for SRID registration

    Returns:
        list of per-table status dicts
    """
    # Ensure SRID is available
    if not ensure_srid(con, srid, sql_file):
        logger.error("Cannot ensure SRID %d, aborting", srid)
        return [{"table": t, "status": "error",
                 "reason": "SRID not available"} for t in table_names]

    results = []
    for table_name in table_names:
        table_lower = table_name.lower()

        # Look up metadata from catalog
        meta = catalog.get(table_lower)
        if meta is None:
            logger.warning("%s: not found in catalog, skipping", table_lower)
            results.append({"table": table_lower, "status": "skipped",
                            "reason": "not in catalog"})
            continue

        geomtype = meta["geomtype"]
        density_attrs = meta["density_attrs"]

        try:
            # Step 1 — reproject
            result = reproject_table(con, table_lower, srid, geomtype, schema)
            results.append(result)

            if result["status"] == "error":
                continue

            # Step 2 — density calculations
            if density_attrs:
                compute_densities_for_table(
                    con, table_lower, srid, schema, density_attrs, geomtype
                )

        except Exception as e:
            logger.error("%s: unexpected error: %s", table_lower, e)
            results.append({"table": table_lower, "status": "error",
                            "reason": str(e)})

    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]):
    """Print a summary table of reprojection results."""
    succeeded = [r for r in results if r["status"] == "success"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] == "error"]

    print(f"\n{'='*55}")
    print(f"Total: {len(results)}  "
          f"Success: {len(succeeded)}  "
          f"Skipped: {len(skipped)}  "
          f"Failed: {len(failed)}")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['table']}: {r.get('reason', '')}")
    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Reproject shapefile tables and compute density columns."
    )
    parser.add_argument(
        "--srid", type=int, required=True,
        help="Target SRID (e.g. 900921)")
    parser.add_argument(
        "--catalog", required=True,
        help="Path to extended shapefile catalog CSV")
    parser.add_argument(
        "--sql-file", default=None,
        help="Path to projection SQL file (for SRID registration)")
    parser.add_argument(
        "--tables", nargs="*", default=None,
        help="Specific tables to process (default: all in catalog)")
    parser.add_argument(
        "--config", default=None,
        help="Path to database_config.csv")
    parser.add_argument(
        "--schema", default="public",
        help="Target PostgreSQL schema (default: public)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Load DB config and connect
    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    # Read catalog
    catalog_path = Path(args.catalog)
    catalog = read_shapefile_catalog(catalog_path)
    logger.info("Catalog: %s (%d shapefiles)", catalog_path, len(catalog))

    # Determine which tables to process
    if args.tables:
        table_names = [t.lower() for t in args.tables]
    else:
        table_names = list(catalog.keys())

    if not table_names:
        logger.warning("No tables to process")
        sys.exit(0)

    # Resolve SQL file: CLI arg > config projection_{srid} key
    sql_file = args.sql_file
    if sql_file is None:
        projections = config.get("spatial", {}).get("projections", {})
        sql_file = projections.get(str(args.srid))

    # Run
    results = reproject_tables(
        con, catalog, table_names, args.srid, args.schema, sql_file
    )
    print_summary(results)
    sys.exit(1 if any(r["status"] == "error" for r in results) else 0)


if __name__ == "__main__":
    main()
