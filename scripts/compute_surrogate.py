"""
Compute spatial surrogates.

Replaces the legacy Java + csh pipeline.  Reads the same CSV
inputs (control_variables, surrogate_generation, surrogate_specification,
shapefile_catalog) and executes PostGIS spatial operations to produce
surrogate ratio tables.

Current scope (Phase 1):
  - polygon geometry with weight attribute (e.g. Population 100)
  - Stage 1 (wp_cty) and Stage 2 (wp_cty_cell) only
  - Stage 3-5 (numer/denom/surg) and file export are not yet implemented

Usage:
    python scripts/compute_surrogate.py 
        --control-file control_variables_pg.quickstart.csv 
        [--config database_config.csv] [--schema public] 
        [--codes 100] [--dry-run]
"""

import argparse
import csv
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import db_utils
import reproject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SurrogateJob — all parameters for one surrogate computation
# ---------------------------------------------------------------------------

@dataclass
class SurrogateJob:
    """All parameters needed to compute one surrogate."""

    # Identity
    region: str               # e.g. "USA"
    surrogate_code: int       # e.g. 100
    surrogate_name: str       # e.g. "Population"

    # Data shapefile (geographic boundaries, e.g. counties)
    data_table: str           # e.g. "cb_2017_us_county_500k"
    data_attribute: str       # e.g. "geoid"

    # Weight shapefile (the values being distributed)
    weight_table: str         # e.g. "acs2016_5yr_bg"
    weight_attribute: str     # e.g. "pop2016", or "" if area/length/count-only

    # Optional SQL filter applied to the weight table
    filter_function: str      # e.g. "" or "moves2014>1 and moves2014<6"

    # Geometry info (from shapefile catalog)
    weight_geomtype: str      # e.g. "MultiPolygon", "MultiLineString", "MultiPoint"

    # Grid and projection
    grid_name: str            # e.g. "us12k_516x444"
    srid: int                 # e.g. 900921

    # Output settings
    output_dir: str
    denominator_threshold: float  # e.g. 0.0005

    # -- Derived properties -------------------------------------------------

    @property
    def geom_family(self) -> str:
        """Return 'polygon', 'line', or 'point' based on weight geometry type."""
        gt = self.weight_geomtype.upper()
        if "POLYGON" in gt:
            return "polygon"
        if "LINE" in gt:
            return "line"
        return "point"

    @property
    def has_weight_attr(self) -> bool:
        return bool(self.weight_attribute)

    @property
    def has_filter(self) -> bool:
        return bool(self.filter_function)

    # Table naming (follows legacy convention)

    @property
    def wp_cty_table(self) -> str:
        return f"wp_cty_{self.surrogate_code}_{self.srid}"

    @property
    def wp_cty_cell_table(self) -> str:
        return f"wp_cty_cell_{self.surrogate_code}_{self.grid_name}"

    @property
    def numer_table(self) -> str:
        return f"numer_{self.surrogate_code}_{self.grid_name}"

    @property
    def denom_table(self) -> str:
        return f"denom_{self.surrogate_code}_{self.grid_name}"

    @property
    def surg_table(self) -> str:
        return f"surg_{self.surrogate_code}_{self.grid_name}"


# ---------------------------------------------------------------------------
# CSV readers
# ---------------------------------------------------------------------------

def read_control_variables(csv_path: Path) -> dict[str, str]:
    """Read control_variables_pg.*.csv → {VARIABLE: VALUE}.

    The file has columns: VARIABLE, VALUE, DESCRIPTION.
    """
    controls = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("VARIABLE", "").strip()
            val = row.get("VALUE", "").strip()
            if key:
                controls[key] = val
    return controls


def read_generation(csv_path: Path) -> list[dict]:
    """Read surrogate_generation_pg.*.csv, return rows where GENERATE=YES."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("GENERATE", "").strip().upper() == "YES":
                rows.append({k: v.strip() for k, v in row.items()})
    return rows


def read_specification(csv_path: Path) -> dict[str, dict]:
    """Read surrogate_specification_pg.*.csv, keyed by '{REGION}_{CODE}'.

    For fast lookup when cross-referencing with generation rows
    """
    specs = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            region = row.get("REGION", "").strip()
            code = row.get("SURROGATE CODE", "").strip()
            if region and code:
                key = f"{region}_{code}"
                specs[key] = {k: v.strip() for k, v in row.items()}
    return specs


# ---------------------------------------------------------------------------
# Job builder — cross-references all CSV inputs
# ---------------------------------------------------------------------------

def build_jobs(
    controls: dict[str, str],
    generations: list[dict],
    specifications: dict[str, dict],
    catalog: dict,
) -> list[SurrogateJob]:
    """Create SurrogateJob instances from cross-referenced CSV data.

    For each generation row (GENERATE=YES):
      1. Look up the surrogate specification
      2. Look up the weight shapefile in the catalog to get geometry type
      3. Combine with run-level settings from control_variables
    """
    grid_name = controls.get("OUTPUT_GRID_NAME", "")
    srid = int(controls.get("SRID_FINAL", "0"))
    output_dir = controls.get("OUTPUT DIRECTORY", "./outputs")
    threshold = float(controls.get("DENOMINATOR_THRESHOLD", "0.0005"))

    jobs = []
    for gen in generations:
        region = gen["REGION"]
        code = gen["SURROGATE CODE"]
        name = gen["SURROGATE"]

        # Look up specification
        spec_key = f"{region}_{code}"
        spec = specifications.get(spec_key)
        if spec is None:
            logger.warning("Surrogate %s (%s): no specification found, skipping",
                           code, name)
            continue

        weight_table = spec.get("WEIGHT SHAPEFILE", "").lower()
        data_table = spec.get("DATA SHAPEFILE", "").lower()

        # Look up weight shapefile in catalog for geometry type
        cat_entry = catalog.get(weight_table)
        if cat_entry is None:
            logger.warning("Surrogate %s (%s): weight shapefile '%s' not in "
                           "catalog, skipping", code, name, weight_table)
            continue

        jobs.append(SurrogateJob(
            region=region,
            surrogate_code=int(code),
            surrogate_name=name,
            data_table=data_table,
            data_attribute=spec.get("DATA ATTRIBUTE", "").lower(),
            weight_table=weight_table,
            weight_attribute=spec.get("WEIGHT ATTRIBUTE", "").strip(),
            filter_function=spec.get("FILTER FUNCTION", "").strip(),
            weight_geomtype=cat_entry["geomtype"],
            grid_name=grid_name,
            srid=srid,
            output_dir=output_dir,
            denominator_threshold=threshold,
        ))

    return jobs


# ---------------------------------------------------------------------------
# Stage 1: wp_cty — intersect weight shapes with data boundaries
# ---------------------------------------------------------------------------
#
# Purpose: clip weight geometries to data-shapefile boundaries (e.g. clip
# census block groups to county boundaries).  After clipping, area also changes,
# so it needs to be recomputed: weight_attr = density × new_area.
#
# When data_table == weight_table the clip is unnecessary — just copy rows.

def create_wp_cty(con, job: SurrogateJob, schema: str = "public"):
    """Stage 1: intersect weight geometries with data boundaries.

    Only the polygon + weight-attribute path is implemented. template_polygon_noFF_withWA.csh
    """
    if job.geom_family != "polygon":
        raise NotImplementedError(
            f"geom_family='{job.geom_family}' not yet supported in create_wp_cty"
        )
    if not job.has_weight_attr:
        raise NotImplementedError(
            "polygon without weight attribute not yet supported in create_wp_cty"
        )

    _create_polygon_wa_wp_cty(con, job, schema)


def _create_polygon_wa_wp_cty(con, job: SurrogateJob, schema: str):
    """Polygon + weight-attribute path for Stage 1."""
    tbl = f"{schema}.{job.wp_cty_table}"
    data = f"{schema}.{job.data_table}"
    weight = f"{schema}.{job.weight_table}"
    wa = job.weight_attribute
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"

    # Drop if exists
    con.raw_sql(f"DROP TABLE IF EXISTS {tbl}")

    # Create table
    con.raw_sql(f"""
        CREATE TABLE {tbl} (
            {da}               varchar(6)       NOT NULL,
            {wa}               double precision DEFAULT 0.0,
            {wa}_dens_{srid}   double precision DEFAULT 0.0,
            area_{srid}        double precision DEFAULT 0.0
        )
    """)
    con.raw_sql(
        f"SELECT AddGeometryColumn('{schema}', '{job.wp_cty_table}', "
        f"'{geom}', {srid}, 'MultiPolygon', 2)"
    )

    # Insert: spatial intersection or direct copy
    if job.data_table == job.weight_table:
        # Same table — no intersection needed
        con.raw_sql(f"""
            INSERT INTO {tbl}
            SELECT {da}, {wa}, {wa}_dens_{srid}, 0.0, {geom}
            FROM {data}
        """)
    else:
        # Different tables — clip weight by data boundaries
        #   ST_CoveredBy:  weight fully inside data → use weight geometry as-is
        #   Otherwise:     compute intersection, extract polygon collection
        #   NOT ST_Touches: exclude edge-only contact (zero-area overlap)
        con.raw_sql(f"""
            INSERT INTO {tbl}
            SELECT {data}.{da},
                   {weight}.{wa},
                   {weight}.{wa}_dens_{srid},
                   0.0,
                   CASE
                       WHEN ST_CoveredBy({weight}.{geom}, {data}.{geom})
                           THEN {weight}.{geom}
                       ELSE ST_CollectionExtract(
                                ST_Multi(ST_Intersection({weight}.{geom},
                                                         {data}.{geom})),
                                3)
                   END
            FROM {data}
            JOIN {weight}
                ON (NOT ST_Touches({weight}.{geom}, {data}.{geom})
                    AND ST_Intersects({weight}.{geom}, {data}.{geom}))
        """)

    # Post-processing (order matters):
    # 1. Fix any invalid geometries produced by intersection
    con.raw_sql(f"""
        UPDATE {tbl}
        SET {geom} = ST_MakeValid({geom})
        WHERE NOT ST_IsValid({geom})
    """)
    # 2. Recompute area after clipping
    con.raw_sql(f"UPDATE {tbl} SET area_{srid} = ST_Area({geom})")
    # 3. Recompute weight = density × new area
    con.raw_sql(f"UPDATE {tbl} SET {wa} = {wa}_dens_{srid} * area_{srid}")
    # 4. Spatial index for Stage 2
    con.raw_sql(f"CREATE INDEX ON {tbl} USING GIST ({geom})")


# ---------------------------------------------------------------------------
# Stage 2: wp_cty_cell — intersect wp_cty with grid cells
# ---------------------------------------------------------------------------
#
# Purpose: overlay Stage 1 results onto the modeling grid.  Same clipping
# pattern as Stage 1 (ST_CoveredBy fast path, then ST_Intersection).
# After clipping to grid cells, area and weight are recomputed again.

def create_wp_cty_cell(con, job: SurrogateJob, schema: str = "public"):
    """Stage 2: intersect wp_cty with grid cells.

    Only the polygon + weight-attribute path is implemented.
    """
    if job.geom_family != "polygon":
        raise NotImplementedError(
            f"geom_family='{job.geom_family}' not yet supported in "
            "create_wp_cty_cell"
        )
    if not job.has_weight_attr:
        raise NotImplementedError(
            "polygon without weight attribute not yet supported in "
            "create_wp_cty_cell"
        )

    _create_polygon_wa_wp_cty_cell(con, job, schema)


def _create_polygon_wa_wp_cty_cell(con, job: SurrogateJob, schema: str):
    """Polygon + weight-attribute path for Stage 2."""
    tbl = f"{schema}.{job.wp_cty_cell_table}"
    wp = f"{schema}.{job.wp_cty_table}"
    grid = f"{schema}.{job.grid_name}"
    wa = job.weight_attribute
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"

    # Drop if exists
    con.raw_sql(f"DROP TABLE IF EXISTS {tbl}")

    # Create table — adds colnum/rownum from grid
    con.raw_sql(f"""
        CREATE TABLE {tbl} (
            {da}               varchar(6)       NOT NULL,
            colnum             integer          NOT NULL,
            rownum             integer          NOT NULL,
            area_{srid}        double precision DEFAULT 1.0,
            {wa}               double precision DEFAULT 0.0,
            {wa}_dens_{srid}   double precision DEFAULT 0.0
        )
    """)
    con.raw_sql(
        f"SELECT AddGeometryColumn('{schema}', '{job.wp_cty_cell_table}', "
        f"'{geom}', {srid}, 'MultiPolygon', 2)"
    )

    # Insert: clip wp_cty geometries to grid cells
    con.raw_sql(f"""
        INSERT INTO {tbl}
        SELECT wp.{da}, g.colnum, g.rownum,
               0.0,
               wp.{wa},
               wp.{wa}_dens_{srid},
               CASE
                   WHEN ST_CoveredBy(wp.{geom}, g.gridcell)
                       THEN wp.{geom}
                   ELSE ST_CollectionExtract(
                            ST_Multi(ST_Intersection(wp.{geom}, g.gridcell)),
                            3)
               END
        FROM {wp} wp
        JOIN {grid} g
            ON (NOT ST_Touches(wp.{geom}, g.gridcell)
                AND ST_Intersects(wp.{geom}, g.gridcell))
    """)

    # Post-processing — same pattern as Stage 1
    con.raw_sql(f"""
        UPDATE {tbl}
        SET {geom} = ST_MakeValid({geom})
        WHERE NOT ST_IsValid({geom})
    """)
    con.raw_sql(f"UPDATE {tbl} SET area_{srid} = ST_Area({geom})")
    con.raw_sql(f"UPDATE {tbl} SET {wa} = {wa}_dens_{srid} * area_{srid}")
    con.raw_sql(f"CREATE INDEX ON {tbl} USING GIST ({geom})")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def compute_surrogate(con, job: SurrogateJob, schema: str = "public") -> dict:
    """Run available stages for one surrogate job.

    Currently runs Stage 1 (wp_cty) and Stage 2 (wp_cty_cell).
    Stage 3-5 and file export are not yet implemented.
    """
    start = time.time()
    logger.info("Computing surrogate %d (%s)...",
                job.surrogate_code, job.surrogate_name)

    try:
        # Stage 1: weight × data intersection
        logger.info("  Stage 1: create_wp_cty (weight-data intersection)")
        t1 = time.time()
        create_wp_cty(con, job, schema)
        cnt1 = con.raw_sql(
            f"SELECT COUNT(*) FROM {schema}.{job.wp_cty_table}"
        ).fetchone()[0]
        logger.info("  Stage 1 complete: %s (%d rows, %.1fs)",
                     job.wp_cty_table, cnt1, time.time() - t1)

        # Stage 2: grid cell intersection
        logger.info("  Stage 2: create_wp_cty_cell (grid intersection)")
        t2 = time.time()
        create_wp_cty_cell(con, job, schema)
        cnt2 = con.raw_sql(
            f"SELECT COUNT(*) FROM {schema}.{job.wp_cty_cell_table}"
        ).fetchone()[0]
        logger.info("  Stage 2 complete: %s (%d rows, %.1fs)",
                     job.wp_cty_cell_table, cnt2, time.time() - t2)

        # TODO: Stage 3-5 (numer, denom, surg) and file export

        elapsed = round(time.time() - start, 1)
        logger.info("Surrogate %d done (%.1fs)", job.surrogate_code, elapsed)
        return {
            "code": job.surrogate_code,
            "name": job.surrogate_name,
            "status": "success",
            "wp_cty_rows": cnt1,
            "wp_cty_cell_rows": cnt2,
            "elapsed": elapsed,
        }

    except Exception as e:
        logger.error("Surrogate %d failed: %s", job.surrogate_code, e)
        return {
            "code": job.surrogate_code,
            "name": job.surrogate_name,
            "status": "error",
            "reason": str(e),
        }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(results: list[dict]):
    """Print a summary of computation results."""
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]

    print(f"\n{'=' * 60}")
    print(f"Total: {len(results)}  Success: {len(succeeded)}  "
          f"Failed: {len(failed)}")
    for r in succeeded:
        print(f"  {r['code']} ({r['name']}): wp_cty={r['wp_cty_rows']} "
              f"wp_cty_cell={r['wp_cty_cell_rows']} ({r['elapsed']}s)")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['code']} ({r['name']}): {r.get('reason', '')}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compute spatial surrogates (Stage 1-2)."
    )
    parser.add_argument(
        "--control-file", required=True,
        help="Path to control_variables_pg.*.csv")
    parser.add_argument(
        "--config", default=None,
        help="Path to database_config.csv")
    parser.add_argument(
        "--schema", default="public",
        help="PostgreSQL schema (default: public)")
    parser.add_argument(
        "--codes", nargs="+", type=int, default=None,
        help="Only compute these surrogate codes (overrides GENERATE=YES)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print job list without executing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # ---- Read all CSV inputs ----

    control_path = Path(args.control_file)
    controls = read_control_variables(control_path)
    logger.info("Control variables loaded from %s", control_path)

    # CSV paths in control_variables are relative to current directory (legacy convention)
    gen_path = Path(controls["GENERATION CONTROL FILE"])
    spec_path = Path(controls["SURROGATE SPECIFICATION FILE"])
    cat_path = Path(controls["SHAPEFILE CATALOG"])

    generations = read_generation(gen_path)
    specifications = read_specification(spec_path)
    catalog = reproject.read_shapefile_catalog(cat_path)
    logger.info("Loaded %d generation rows (GENERATE=YES), "
                "%d specifications, %d catalog entries",
                len(generations), len(specifications), len(catalog))

    # ---- Build job list ----

    jobs = build_jobs(controls, generations, specifications, catalog)

    if args.codes:
        jobs = [j for j in jobs if j.surrogate_code in args.codes]

    if not jobs:
        logger.warning("No jobs to run")
        sys.exit(0)

    # ---- Dry run: just print jobs ----

    if args.dry_run:
        print(f"\n{len(jobs)} job(s) would run:\n")
        for j in jobs:
            print(f"  Code {j.surrogate_code}: {j.surrogate_name}")
            print(f"    data:   {j.data_table}.{j.data_attribute}")
            print(f"    weight: {j.weight_table}"
                  f"{'.' + j.weight_attribute if j.has_weight_attr else ''}")
            print(f"    geom:   {j.weight_geomtype} → {j.geom_family}")
            print(f"    grid:   {j.grid_name}  SRID: {j.srid}")
            if j.has_filter:
                print(f"    filter: {j.filter_function}")
            print()
        sys.exit(0)

    # ---- Connect and compute ----

    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    try:
        results = []
        for job in jobs:
            result = compute_surrogate(con, job, args.schema)
            results.append(result)
        print_summary(results)
    finally:
        con.disconnect()

    any_failed = any(r["status"] == "error" for r in results)
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
