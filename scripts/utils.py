"""
Utilities.

Usage:
    python scripts/utils.py shapefiles [--config PATH] [--schema SCHEMA]
    python scripts/utils.py shapefiles-ibis [--config PATH] [--schema SCHEMA]
    python scripts/utils.py grids [--config PATH] [--schema SCHEMA]
"""

import argparse
import logging
import sys

import ibis.expr.datatypes as dt

import db_utils

logger = logging.getLogger(__name__)

GEOMETRY_COLUMN_CANDIDATES = {
    # PostGIS tables loaded through ogr2ogr typically land on wkb_geometry.
    "postgres": ("wkb_geometry", "geometry", "geom"),
    # Keep the non-Postgres defaults so future backends can reuse
    "duckdb": ("geom", "geometry", "wkb_geometry"),
    "sqlite": ("geom", "geometry", "wkb_geometry"),
    "default": ("wkb_geometry", "geometry", "geom"),
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def classify_shapefile_columns(col_names: list[str]) -> tuple[list[str], list[str]]:
    """Split non-base columns into reprojection and density groups."""
    reprojections = [c for c in col_names if c.startswith("geom_")]
    density = [c for c in col_names
               if c.startswith("area_") or c.startswith("length_")
               or "_dens_" in c]
    return reprojections, density


def get_ibis_geom_info(schema_obj, geom_col: str = "wkb_geometry") -> tuple[str, str]:
    """Extract geometry type and SRID from an ibis schema dtype when available."""
    dtype = schema_obj[geom_col]
    geom_type = type(dtype).__name__.upper()
    srid = getattr(dtype, "srid", None)
    return geom_type, ("n/a" if srid is None else str(srid))


def get_backend_name(con) -> str:
    """Return the ibis backend name with a stable fallback."""
    return getattr(con, "name", None) or "default"


def resolve_geometry_column(con, schema_obj) -> str | None:
    """
    Resolve the base geometry column without consulting backend metadata tables.

    Prefer backend-specific naming conventions first, then fall back to the
    first geospatial dtype exposed by ibis for unknown backends.
    """
    backend = get_backend_name(con)
    col_names = list(schema_obj.names)
    candidates = GEOMETRY_COLUMN_CANDIDATES.get(backend)

    for col_name in candidates or GEOMETRY_COLUMN_CANDIDATES["default"]:
        if col_name in col_names:
            return col_name

    if candidates is not None:
        return None

    for col_name in col_names:
        dtype = schema_obj[col_name]
        if isinstance(dtype, dt.GeoSpatial):
            return col_name
        is_geospatial = getattr(dtype, "is_geospatial", None)
        if callable(is_geospatial) and is_geospatial():
            return col_name

    return None


def print_shapefile_summary(table_info: list[dict], schema: str):
    """Print a standard shapefile summary table."""
    if not table_info:
        print("No shapefiles loaded")
        return

    print(f"\nLoaded shapefiles (schema: {schema})\n")
    hdr = (f"{'Shapefile':<35} {'Geom Type':<20} {'SRID':<6} "
           f"{'Rows':<9} {'Reprojections':<25} Density")
    print(hdr)
    print("-" * len(hdr))

    for t in table_info:
        reproj = ", ".join(t["reprojections"]) if t["reprojections"] else "(none)"
        dens = ", ".join(t["density"]) if t["density"] else "(none)"
        print(f"{t['name']:<35} {t['geom_type']:<20} {t['srid']:<6} "
              f"{t['rows']:<9} {reproj:<25} {dens}")

    print(f"\n{len(table_info)} shapefiles loaded")


# ---------------------------------------------------------------------------
# List loaded shapefiles - legacy raw SQL / PostGIS
# ---------------------------------------------------------------------------

def list_shapefiles(con, schema: str = "public"):
    """Query and print loaded shapefile tables using PostGIS metadata."""

    # Find shapefile tables (identified by wkb_geometry column)
    rows = con.raw_sql(f"""
        SELECT f_table_name, type, srid
        FROM geometry_columns
        WHERE f_table_schema = '{schema}'
          AND f_geometry_column = 'wkb_geometry'
        ORDER BY f_table_name
    """).fetchall()

    if not rows:
        print("No shapefiles loaded")
        return

    # For each table, get columns and row count
    table_info = []
    for table_name, geom_type, srid in rows:
        # Row count
        cnt = con.raw_sql(
            f"SELECT COUNT(*) FROM {schema}.{table_name}"
        ).fetchone()[0]

        # All columns except wkb_geometry
        cols = con.raw_sql(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = '{schema}'
              AND table_name = '{table_name}'
              AND column_name != 'wkb_geometry'
            ORDER BY ordinal_position
        """).fetchall()
        col_names = [c[0] for c in cols]

        reprojections, density = classify_shapefile_columns(col_names)

        table_info.append({
            "name": table_name,
            "geom_type": geom_type,
            "srid": srid,
            "rows": cnt,
            "reprojections": reprojections,
            "density": density,
        })

    print_shapefile_summary(table_info, schema)


# ---------------------------------------------------------------------------
# List loaded shapefiles - generic ibis
# ---------------------------------------------------------------------------

def list_shapefiles_ibis(con, schema: str = "public"):
    """Query and print shapefile-like tables using generic ibis APIs."""

    table_info = []
    for table_name in sorted(con.list_tables(database=schema)):
        table = con.table(table_name, database=schema)
        schema_obj = table.schema()
        col_names = list(schema_obj.names)
        geom_col = resolve_geometry_column(con, schema_obj)
        if geom_col is None:
            continue

        other_cols = [c for c in col_names if c != geom_col]
        reprojections, density = classify_shapefile_columns(other_cols)
        geom_type, srid = get_ibis_geom_info(schema_obj, geom_col)

        table_info.append({
            "name": table_name,
            "geom_type": geom_type,
            "srid": srid,
            "rows": table.count().execute(),
            "reprojections": reprojections,
            "density": density,
        })

    print_shapefile_summary(table_info, schema)


GRID_COLUMNS = {"colnum", "rownum", "gridcell"}


# ---------------------------------------------------------------------------
# List grid tables - generic ibis
# ---------------------------------------------------------------------------

def list_grids_ibis(con, schema: str = "public"):
    """Query and print grid tables using generic ibis APIs.

    Grid tables are identified by having colnum, rownum, and gridcell columns
    (the schema produced by generate_grid.py).
    """
    table_info = []
    for table_name in sorted(con.list_tables(database=schema)):
        table = con.table(table_name, database=schema)
        schema_obj = table.schema()
        col_names = set(schema_obj.names)
        if not GRID_COLUMNS.issubset(col_names):
            continue

        _, srid = get_ibis_geom_info(schema_obj, "gridcell")
        agg = table.aggregate(
            cells=table.count(),
            ncols=table.colnum.max(),
            nrows=table.rownum.max(),
        ).execute()
        row = agg.iloc[0]

        table_info.append({
            "name": table_name,
            "srid": srid,
            "ncols": int(row["ncols"]),
            "nrows": int(row["nrows"]),
            "cells": int(row["cells"]),
        })

    print_grid_summary(table_info, schema)


def print_grid_summary(table_info: list[dict], schema: str):
    """Print a standard grid summary table."""
    if not table_info:
        print("No grids loaded")
        return

    print(f"\nLoaded grids (schema: {schema})\n")
    hdr = f"{'Grid':<35} {'SRID':<8} {'Cols x Rows':<15} {'Cells':<10}"
    print(hdr)
    print("-" * len(hdr))

    for t in table_info:
        dims = f"{t['ncols']} x {t['nrows']}"
        print(f"{t['name']:<35} {t['srid']:<8} {dims:<15} {t['cells']:<10}")

    print(f"\n{len(table_info)} grids loaded")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Database inspection utilities."
    )
    sub = parser.add_subparsers(dest="command")

    # shapefiles subcommand
    sp = sub.add_parser(
        "shapefiles",
        help="Legacy raw SQL / PostGIS implementation",
    )
    sp.add_argument("--config", default=None, help="Path to database_config.csv")
    sp.add_argument("--schema", default="public", help="Schema (default: public)")

    sp_ibis = sub.add_parser(
        "shapefiles-ibis",
        help="Generic ibis implementation using wkb_geometry detection",
    )
    sp_ibis.add_argument("--config", default=None, help="Path to database_config.csv")
    sp_ibis.add_argument("--schema", default="public", help="Schema (default: public)")

    sp_grids = sub.add_parser(
        "grids",
        help="List grid tables (ibis-based, detects colnum/rownum/gridcell)",
    )
    sp_grids.add_argument("--config", default=None, help="Path to database_config.csv")
    sp_grids.add_argument("--schema", default="public", help="Schema (default: public)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    try:
        if args.command == "shapefiles":
            list_shapefiles(con, args.schema)
        elif args.command == "shapefiles-ibis":
            list_shapefiles_ibis(con, args.schema)
        elif args.command == "grids":
            list_grids_ibis(con, args.schema)
    finally:
        con.disconnect()


if __name__ == "__main__":
    main()
