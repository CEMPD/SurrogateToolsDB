"""
Utilities.

Usage:
    python scripts/utils.py shapefiles [--config PATH] [--schema SCHEMA]
"""

import argparse
import logging
import sys

import db_utils

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# List loaded shapefiles
# ---------------------------------------------------------------------------

def list_shapefiles(con, schema: str = "public"):
    """Query and print loaded shapefile tables."""

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

        # Split into reprojections (geom_*) and density (area_*, length_*, *_dens_*)
        reprojections = [c for c in col_names if c.startswith("geom_")]
        density = [c for c in col_names
                   if c.startswith("area_") or c.startswith("length_")
                   or "_dens_" in c]

        table_info.append({
            "name": table_name,
            "geom_type": geom_type,
            "srid": srid,
            "rows": cnt,
            "reprojections": reprojections,
            "density": density,
        })

    # Print
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
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Database inspection utilities."
    )
    sub = parser.add_subparsers(dest="command")

    # shapefiles subcommand
    sp = sub.add_parser("shapefiles", help="List loaded shapefiles")
    sp.add_argument("--config", default=None, help="Path to database_config.csv")
    sp.add_argument("--schema", default="public", help="Schema (default: public)")

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
    finally:
        con.disconnect()


if __name__ == "__main__":
    main()
