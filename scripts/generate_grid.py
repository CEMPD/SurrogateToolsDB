"""
Generate modeling grid table(s) in PostGIS.

Reads grid definition from a standard IOAPI GRIDDESC file, auto-resolves
the projection to a spatial_ref_sys SRID (Lambert Conformal only for now),
and creates a table of grid cell polygons using PostGIS raster functions.

Usage:
    python scripts/generate_grid.py --griddesc GRIDDESC.txt --grid-name us12k_516x444
                                    [--config database_config.csv]
                                    [--schema public]
"""

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import db_utils

logger = logging.getLogger(__name__)

# Hardcoded sphere: IOAPI_ISPH entry 20 (Normal Sphere, MM5/WRF-ARW)
SPHERE_A = 6370000.0
SPHERE_B = 6370000.0


# ---------------------------------------------------------------------------
# GRIDDESC parser
# ---------------------------------------------------------------------------

def parse_griddesc(filepath: Path) -> tuple[dict, dict]:
    """
    Parse an IOAPI GRIDDESC file.

    The file has two segments separated by lines containing only ' '
    (a quoted blank string).

    Segment 1 — Coordinate system definitions:
        'COORD_NAME'
        COORDTYPE, P_ALP, P_BET, P_GAM, XCENT, YCENT

    Segment 2 — Grid definitions:
        'GRID_NAME'
        'COORD_NAME', XORIG, YORIG, XCELL, YCELL, NCOLS, NROWS, NTHIK

    Values may be separated by spaces or commas and can span multiple lines.

    Returns:
        (coord_systems, grids) where:
        - coord_systems: dict keyed by name, e.g.
          {"LAM_40N97W": {"coordtype": 2, "p_alp": 33.0, ...}}
        - grids: dict keyed by name, e.g.
          {"us12k_516x444": {"coord_name": "LAM_40N97W", "xorig": ..., ...}}
    """
    text = filepath.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Split into segments by ' ' delimiter lines
    segments = []
    current = []
    for line in lines:
        stripped = line.strip()
        if stripped in ("' '", "''", '"\\ "', '" "'):
            if current:
                segments.append(current)
            current = []
        else:
            if stripped:
                current.append(stripped)
    if current:
        segments.append(current)

    if len(segments) < 2:
        raise ValueError(
            f"{filepath}: expected at least 2 segments in GRIDDESC file, "
            f"found {len(segments)}"
        )

    coord_systems = _parse_coord_segment(segments[0])
    grids = _parse_grid_segment(segments[1])

    return coord_systems, grids


def _extract_quoted_name(s: str) -> str:
    """Extract a name from a quoted string like 'LAM_40N97W' or \"LAM_40N97W\"."""
    match = re.match(r"""['"](.+?)['"]""", s.strip())
    if match:
        return match.group(1)
    return s.strip()


def _parse_numbers(text: str) -> list[float]:
    """Parse space/comma separated numbers from a string."""
    # Replace commas with spaces, then split
    cleaned = text.replace(",", " ")
    return [float(x) for x in cleaned.split() if x]


def _parse_coord_segment(lines: list[str]) -> dict:
    """Parse coordinate system definitions from segment 1 lines."""
    coord_systems = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for a quoted name
        if line.startswith("'") or line.startswith('"'):
            name = _extract_quoted_name(line)
            # Collect numeric values from subsequent lines
            nums = []
            i += 1
            while i < len(lines) and not (lines[i].strip().startswith("'") or
                                           lines[i].strip().startswith('"')):
                nums.extend(_parse_numbers(lines[i]))
                i += 1
            if len(nums) >= 6:
                coord_systems[name] = {
                    "coordtype": int(nums[0]),
                    "p_alp": nums[1],
                    "p_bet": nums[2],
                    "p_gam": nums[3],
                    "xcent": nums[4],
                    "ycent": nums[5],
                }
            else:
                logger.warning("Coordinate system '%s': expected 6 params, "
                               "got %d", name, len(nums))
        else:
            i += 1

    return coord_systems


def _parse_grid_segment(lines: list[str]) -> dict:
    """Parse grid definitions from segment 2 lines."""
    grids = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for a quoted grid name
        if line.startswith("'") or line.startswith('"'):
            grid_name = _extract_quoted_name(line)
            # Next line(s): coord_name + numeric params
            i += 1
            # Collect all remaining text until next quoted-only name line
            raw = []
            while i < len(lines):
                next_line = lines[i].strip()
                # If this line starts with a quote and contains ONLY a quoted
                # name (no numbers), it's the next grid entry
                if (next_line.startswith("'") or next_line.startswith('"')):
                    # Check if it has numbers after the quoted name
                    after_quote = re.sub(r"""['"][^'"]+['"]""", "", next_line).strip()
                    after_quote = after_quote.lstrip(",").strip()
                    if not after_quote:
                        # Pure name line — next grid entry
                        break
                raw.append(next_line)
                i += 1

            # Parse: first token is coord_name (quoted), rest are numbers
            combined = " ".join(raw)
            coord_match = re.match(r"""['"](.+?)['"](.*)""", combined)
            if coord_match:
                coord_name = coord_match.group(1)
                nums = _parse_numbers(coord_match.group(2))
                if len(nums) >= 7:
                    grids[grid_name] = {
                        "coord_name": coord_name,
                        "xorig": nums[0],
                        "yorig": nums[1],
                        "xcell": nums[2],
                        "ycell": nums[3],
                        "ncols": int(nums[4]),
                        "nrows": int(nums[5]),
                        "nthik": int(nums[6]),
                    }
                else:
                    logger.warning("Grid '%s': expected 7 params, got %d",
                                   grid_name, len(nums))
        else:
            i += 1

    return grids


# ---------------------------------------------------------------------------
# Lambert Conformal → SRID
# ---------------------------------------------------------------------------

def build_lambert_proj4(p_alp: float, p_bet: float, p_gam: float,
                        ycent: float) -> str:
    """Build proj4text for Lambert Conformal Conic from GRIDDESC params."""
    return (
        f"+proj=lcc +lat_1={p_alp} +lat_2={p_bet} "
        f"+lat_0={ycent} +lon_0={p_gam} "
        f"+x_0=0 +y_0=0 +a={SPHERE_A:.0f} +b={SPHERE_B:.0f} "
        f"+units=m +no_defs"
    )


def build_lambert_srtext(p_alp: float, p_bet: float, p_gam: float,
                         ycent: float) -> str:
    """Build WKT srtext for Lambert Conformal Conic from GRIDDESC params."""
    return (
        'PROJCS["Lambert_Conformal_Conic",'
        'GEOGCS["GCS_Sphere_WRF",'
        f'DATUM["Sphere_WRF",SPHEROID["Sphere_WRF",{SPHERE_A:.1f},0.0]],'
        'PRIMEM["Greenwich",0.0],'
        'UNIT["Degree",0.0174532925199433]],'
        'PROJECTION["Lambert_Conformal_Conic_2SP"],'
        'PARAMETER["false_easting",0.0],'
        'PARAMETER["false_northing",0.0],'
        f'PARAMETER["central_meridian",{p_gam}],'
        f'PARAMETER["standard_parallel_1",{p_alp}],'
        f'PARAMETER["standard_parallel_2",{p_bet}],'
        f'PARAMETER["latitude_of_origin",{ycent}],'
        'UNIT["Meter",1.0]]'
    )


def _fmt(val: float) -> str:
    """Format a number: drop trailing .0 for integers."""
    return str(int(val)) if val == int(val) else str(val)


def resolve_srid(con, coord: dict) -> int:
    """
    Resolve a GRIDDESC coordinate system to a spatial_ref_sys SRID.

    For Lambert Conformal (coordtype=2):
      1. Search spatial_ref_sys for matching proj4text by key parameters
      2. If not found, build proj4text/srtext and insert a new entry

    For other types: error (not yet supported).

    Returns:
        SRID integer
    """
    if coord["coordtype"] != 2:
        raise ValueError(
            f"Coordinate type {coord['coordtype']} is not supported. "
            f"Only Lambert Conformal Conic (type 2) is currently supported. "
            f"For other projections, register the SRID manually."
        )

    p_alp = coord["p_alp"]
    p_bet = coord["p_bet"]
    p_gam = coord["p_gam"]
    ycent = coord["ycent"]

    # Search for existing match by key parameters
    result = con.raw_sql(f"""
        SELECT srid FROM spatial_ref_sys
        WHERE proj4text LIKE '%+proj=lcc%'
          AND proj4text LIKE '%+lat_1={_fmt(p_alp)}%'
          AND proj4text LIKE '%+lat_2={_fmt(p_bet)}%'
          AND proj4text LIKE '%+lon_0={_fmt(p_gam)}%'
          AND proj4text LIKE '%+lat_0={_fmt(ycent)}%'
          AND proj4text LIKE '%+a={SPHERE_A:.0f}%'
    """).fetchone()

    if result:
        srid = result[0]
        logger.info("Found matching SRID: %d", srid)
        return srid

    # Not found — create new entry
    proj4 = build_lambert_proj4(p_alp, p_bet, p_gam, ycent)
    srtext = build_lambert_srtext(p_alp, p_bet, p_gam, ycent)

    max_row = con.raw_sql(
        "SELECT COALESCE(MAX(srid) + 1, 1) FROM spatial_ref_sys"
    ).fetchone()
    new_srid = max_row[0]

    con.raw_sql(f"""
        INSERT INTO spatial_ref_sys (srid, srtext, proj4text)
        VALUES ({new_srid}, '{srtext}', '{proj4}')
    """)

    logger.info("Creating new SRID: %d", new_srid)
    return new_srid


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
              xcell, ycell, ncols, nrows

    Returns:
        status dict, e.g. {"name": ..., "status": "success", "rows": 229104}
    """
    name = grid["name"]
    schema = grid["schema"]
    srid = grid["srid"]
    start = time.time()

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
                        {grid['ncols']}, {grid['nrows']},
                        {grid['xorig']}, {grid['yorig']},
                        {grid['xcell']}, {grid['ycell']},
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
        description="Generate modeling grid table in PostGIS from GRIDDESC."
    )
    parser.add_argument(
        "--griddesc", required=True,
        help="Path to IOAPI GRIDDESC file")
    parser.add_argument(
        "--grid-name", required=True,
        help="Name of the grid to generate (as defined in GRIDDESC)")
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

    # Parse GRIDDESC
    griddesc_path = Path(args.griddesc)
    coord_systems, grids = parse_griddesc(griddesc_path)
    logger.info("GRIDDESC: %s (%d coord systems, %d grids)",
                griddesc_path, len(coord_systems), len(grids))

    # Find requested grid
    if args.grid_name not in grids:
        logger.error("Grid '%s' not found in GRIDDESC. Available: %s",
                     args.grid_name, ", ".join(grids.keys()))
        sys.exit(1)

    grid_def = grids[args.grid_name]
    coord_name = grid_def["coord_name"]

    if coord_name not in coord_systems:
        logger.error("Coordinate system '%s' (used by grid '%s') "
                     "not found in GRIDDESC", coord_name, args.grid_name)
        sys.exit(1)

    coord = coord_systems[coord_name]
    logger.info("Grid '%s' uses coordinate system '%s' (type %d)",
                args.grid_name, coord_name, coord["coordtype"])

    # Load DB config and connect
    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    # Resolve SRID
    try:
        srid = resolve_srid(con, coord)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # Build grid dict for create_grid()
    grid = {
        "name":   args.grid_name,
        "schema": args.schema,
        "srid":   srid,
        "xorig":  grid_def["xorig"],
        "yorig":  grid_def["yorig"],
        "xcell":  grid_def["xcell"],
        "ycell":  grid_def["ycell"],
        "ncols":  grid_def["ncols"],
        "nrows":  grid_def["nrows"],
    }

    # Run
    try:
        result = create_grid(con, grid)
    except Exception as e:
        logger.error("%s: unexpected error: %s", args.grid_name, e)
        result = {"name": args.grid_name, "status": "error",
                  "reason": str(e)}

    print_summary([result])
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
