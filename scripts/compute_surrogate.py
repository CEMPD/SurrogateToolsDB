"""
Compute spatial surrogates.

Replaces the legacy Java + csh pipeline.  Reads the same CSV
inputs (control_variables, surrogate_generation, surrogate_specification,
shapefile_catalog) and executes PostGIS spatial operations to produce
surrogate ratio tables.

Current scope (Phase 1):
  - polygon geometry with and without weight attributes
  - point geometry without weight attributes
  - Stage 1 (wp_cty), Stage 2 (wp_cty_cell), Stage 3 (numer),
    Stage 4 (denom), and Stage 5 (surg)
  - file export (`NOFILL`, `SRGDESC`) for computed surrogates
  - combined output, merge, gapfill, and QA are not yet implemented

Usage:
    python scripts/compute_surrogate.py 
        --control-file control_variables_pg.quickstart.csv 
        [--config database_config.csv] [--schema public] 
        [--codes 100] [--dry-run]
"""

import argparse
import csv
import getpass
import logging
import platform
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import ibis
import ibis.expr.datatypes as dt

import db_utils
import generate_grid
import reproject
import utils as surrogate_utils

logger = logging.getLogger(__name__)


def is_legacy_none(value: str) -> bool:
    """Return True when a legacy CSV field means 'not specified'."""
    return not value or value.strip().upper() == "NONE"


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
    weight_attribute: str     # e.g. "pop2016", "" or "NONE" for legacy no-WA
    weight_function: str      # legacy metadata field, currently informational

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
        return not is_legacy_none(self.weight_attribute)

    @property
    def weight_attribute_column(self) -> str:
        """Return a validated weight-attribute column name for WA-only paths."""
        if not self.has_weight_attr:
            raise ValueError(
                "weight attribute is NONE/blank for this surrogate"
            )
        return self.weight_attribute

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

    @property
    def nofill_file_name(self) -> str:
        return f"{self.region}_{self.surrogate_code}_NOFILL.txt"

    @property
    def nofill_path(self) -> Path:
        return Path(self.output_dir) / self.nofill_file_name


@dataclass(frozen=True)
class OutputConfig:
    """Run-level settings needed for file export."""

    grid_header: str
    control_file_label: str
    generation_file_label: str
    specification_file_label: str
    shapefile_catalog_label: str
    surrogate_code_file_label: str
    griddesc_label: str
    srgdesc_path: Path | None
    overwrite_output_files: bool
    combined_output_path: Path | None
    cleanup_intermediate_tables: bool


@dataclass(frozen=True)
class SurrogateOutput:
    """One exported surrogate record for SRGDESC output."""

    region: str
    surrogate_code: int
    surrogate_name: str
    nofill_path: Path


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
            weight_function=spec.get("WEIGHT FUNCTION", "").strip(),
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

def get_backend_name(con) -> str:
    """Return the ibis backend name."""
    return getattr(con, "name", None) or "default"


def table_exists(con, table_name: str, schema: str) -> bool:
    """Return True when the target relation name already exists."""
    try:
        return table_name in set(con.list_tables(database=schema))
    except Exception:
        return False


def load_table_expr(con, table_name: str, schema: str):
    """Load a database table as an ibis expression."""
    return con.table(table_name, database=schema)


def ensure_columns(table_expr, table_name: str, required: list[str]):
    """Raise a clear error if a table is missing required columns."""
    col_names = set(table_expr.schema().names)
    missing = [col for col in required if col not in col_names]
    if missing:
        raise ValueError(
            f"{table_name}: missing required columns: {', '.join(missing)}"
        )


def ensure_geospatial_column(table_expr, table_name: str, column_name: str):
    """Raise a clear error if a column is not geospatial by ibis."""
    dtype = table_expr.schema()[column_name]
    if isinstance(dtype, dt.GeoSpatial):
        return

    is_geospatial = getattr(dtype, "is_geospatial", None)
    if callable(is_geospatial) and is_geospatial():
        return

    raise TypeError(
        f"{table_name}.{column_name}: expected GeoSpatial, got {dtype}"
    )


def materialize_table(con, table_name: str, expr, schema: str):
    """Create or replace a table from an ibis expression."""
    if table_exists(con, table_name, schema):
        logger.warning(
            "Existing table will be overwritten: backend=%s schema=%s table=%s",
            get_backend_name(con),
            schema,
            table_name,
        )
    con.create_table(table_name, obj=expr, database=schema, overwrite=True)


def get_table_row_count(con, table_name: str, schema: str) -> int:
    """Return a row count using the generic ibis table API."""
    return int(load_table_expr(con, table_name, schema).count().execute())


class FilterParseError(ValueError):
    """Raised when a FILTER FUNCTION cannot be parsed into an ibis predicate."""


@dataclass(frozen=True)
class FilterToken:
    """One token from a FILTER FUNCTION expression."""

    kind: str
    value: str
    pos: int


_FILTER_TOKEN_RE = re.compile(
    r"""
    (?P<SPACE>\s+)
    | (?P<STRING>'(?:''|[^'])*')
    | (?P<NUMBER>-?\d+(?:\.\d+)?)
    | (?P<OP><>|!=|>=|<=|=|>|<)
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    | (?P<COMMA>,)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<MISMATCH>.)
    """,
    re.VERBOSE,
)


def tokenize_filter_function(filter_sql: str) -> list[FilterToken]:
    """Tokenize a legacy FILTER FUNCTION string."""
    tokens: list[FilterToken] = []
    for match in _FILTER_TOKEN_RE.finditer(filter_sql):
        kind = match.lastgroup
        assert kind is not None

        if kind == "SPACE":
            continue
        if kind == "MISMATCH":
            bad = match.group()
            raise FilterParseError(
                f"unsupported token {bad!r} at position {match.start()}"
            )

        value = match.group()
        if kind == "IDENT":
            upper = value.upper()
            if upper in {"AND", "OR", "IN"}:
                kind = upper
            elif upper == "NOT":
                raise FilterParseError(
                    "FILTER FUNCTION does not support NOT yet"
                )

        tokens.append(FilterToken(kind, value, match.start()))

    tokens.append(FilterToken("EOF", "", len(filter_sql)))
    return tokens


def resolve_filter_column(table_expr, table_name: str, column_name: str):
    """Resolve a FILTER FUNCTION column name against a table schema."""
    schema_names = list(table_expr.schema().names)
    if column_name in schema_names:
        return table_expr[column_name]

    folded = [name for name in schema_names if name.lower() == column_name.lower()]
    if len(folded) == 1:
        return table_expr[folded[0]]
    if len(folded) > 1:
        raise FilterParseError(
            f"{table_name}: ambiguous FILTER FUNCTION column {column_name!r}"
        )

    raise FilterParseError(
        f"{table_name}: FILTER FUNCTION references unknown column {column_name!r}"
    )


class FilterParser:
    """Recursive-descent parser for the supported FILTER FUNCTION subset."""

    def __init__(self, table_expr, table_name: str, tokens: list[FilterToken]):
        self.table_expr = table_expr
        self.table_name = table_name
        self.tokens = tokens
        self.index = 0

    def current(self) -> FilterToken:
        return self.tokens[self.index]

    def advance(self) -> FilterToken:
        token = self.current()
        self.index += 1
        return token

    def match(self, *kinds: str) -> FilterToken | None:
        token = self.current()
        if token.kind in kinds:
            self.index += 1
            return token
        return None

    def expect(self, *kinds: str) -> FilterToken:
        token = self.current()
        if token.kind not in kinds:
            expected = " or ".join(kinds)
            raise FilterParseError(
                f"expected {expected} at position {token.pos}, got {token.value!r}"
            )
        self.index += 1
        return token

    def parse(self):
        expr = self.parse_or()
        self.expect("EOF")
        return expr

    def parse_or(self):
        expr = self.parse_and()
        while self.match("OR"):
            expr = expr | self.parse_and()
        return expr

    def parse_and(self):
        expr = self.parse_primary()
        while self.match("AND"):
            expr = expr & self.parse_primary()
        return expr

    def parse_primary(self):
        if self.match("LPAREN"):
            expr = self.parse_or()
            self.expect("RPAREN")
            return expr
        return self.parse_predicate()

    def parse_predicate(self):
        column_token = self.expect("IDENT")
        column = resolve_filter_column(
            self.table_expr, self.table_name, column_token.value
        )

        if self.match("IN"):
            self.expect("LPAREN")
            values = [self.parse_literal()]
            while self.match("COMMA"):
                values.append(self.parse_literal())
            self.expect("RPAREN")
            return column.isin(values)

        op_token = self.expect("OP")
        value = self.parse_literal()
        return self.apply_comparison(column, op_token.value, value, op_token.pos)

    def parse_literal(self) -> Any:
        token = self.current()
        if token.kind == "NUMBER":
            self.advance()
            return float(token.value) if "." in token.value else int(token.value)
        if token.kind == "STRING":
            self.advance()
            return token.value[1:-1].replace("''", "'")
        if token.kind == "IDENT":
            upper = token.value.upper()
            if upper == "TRUE":
                self.advance()
                return True
            if upper == "FALSE":
                self.advance()
                return False
            if upper == "NULL":
                raise FilterParseError(
                    "FILTER FUNCTION does not support NULL comparisons yet"
                )

        raise FilterParseError(
            f"expected literal at position {token.pos}, got {token.value!r}"
        )

    @staticmethod
    def apply_comparison(column, operator: str, value: Any, pos: int):
        """Convert a comparison operator into the matching ibis expression."""
        if operator == "=":
            return column == value
        if operator in {"!=", "<>"}:
            return column != value
        if operator == ">":
            return column > value
        if operator == ">=":
            return column >= value
        if operator == "<":
            return column < value
        if operator == "<=":
            return column <= value

        raise FilterParseError(
            f"unsupported comparison operator {operator!r} at position {pos}"
        )


def build_filter_predicate(table_expr, filter_sql: str, table_name: str):
    """Parse a FILTER FUNCTION string into an ibis boolean predicate."""
    tokens = tokenize_filter_function(filter_sql)
    return FilterParser(table_expr, table_name, tokens).parse()


def apply_filter_function(table_expr, filter_sql: str, table_name: str):
    """Apply a supported FILTER FUNCTION to a table expression."""
    if not filter_sql:
        return table_expr

    logger.info("  Applying FILTER FUNCTION to %s: %s", table_name, filter_sql)
    predicate = build_filter_predicate(table_expr, filter_sql, table_name)
    return table_expr.filter(predicate)


def get_effective_measure_column(job: SurrogateJob) -> str:
    """Return the Stage 3/4 measure column for the current rebuild scope.

    Current scope supports polygon jobs with either:
      - weight attribute: use the clipped/recomputed value column
      - no weight attribute: use clipped polygon area

    Future template families will need a wider abstraction than "which column
    to sum": Stage 4 especially may vary by source table and by expression
    (for example weighted line length from the original weight table).
    """
    if job.geom_family == "polygon":
        if job.has_weight_attr:
            return job.weight_attribute_column
        return f"area_{job.srid}"

    raise NotImplementedError(
        "effective Stage 3/4 measure column is not defined yet for "
        f"geom_family='{job.geom_family}'"
    )


def get_numer_measure_column(job: SurrogateJob) -> str:
    """Return the Stage 3 column for the current job."""
    if job.geom_family == "point" and not job.has_weight_attr:
        return "count_wp_cty_cell"
    return get_effective_measure_column(job)


def get_denom_measure_column(job: SurrogateJob) -> str:
    """Return the Stage 4 column for the current job."""
    if job.geom_family == "point" and not job.has_weight_attr:
        return "count_wp_cty"
    return get_effective_measure_column(job)


def postprocess_polygon_output(
    con,
    table_name: str,
    geom_col: str,
    weight_col: str | None,
    srid: int,
    schema: str = "public",
):
    """Run PostGIS-only polygon repair and derived-column updates."""
    backend = get_backend_name(con)
    if backend != "postgres":
        logger.info(
            "%s: backend '%s' - skipping PostGIS-only geometry repair and index",
            table_name,
            backend,
        )
        return

    qualified = f"{schema}.{table_name}"
    area_col = f"area_{srid}"

    con.raw_sql(f"""
        UPDATE {qualified}
        SET {geom_col} = ST_CollectionExtract(
            ST_Multi(ST_SetSRID({geom_col}, {srid})),
            3
        )
        WHERE {geom_col} IS NOT NULL
    """)
    con.raw_sql(f"""
        UPDATE {qualified}
        SET {geom_col} = ST_MakeValid({geom_col})
        WHERE {geom_col} IS NOT NULL
          AND NOT ST_IsValid({geom_col})
    """)
    con.raw_sql(f"""
        ALTER TABLE {qualified}
        ALTER COLUMN {geom_col}
        TYPE geometry(MultiPolygon, {srid})
        USING CASE
            WHEN {geom_col} IS NULL THEN NULL
            ELSE ST_Multi(
                ST_CollectionExtract(
                    ST_SetSRID({geom_col}, {srid}),
                    3
                )
            )
        END
    """)
    con.raw_sql(f"UPDATE {qualified} SET {area_col} = ST_Area({geom_col})")
    if weight_col:
        dens_col = f"{weight_col}_dens_{srid}"
        con.raw_sql(
            f"UPDATE {qualified} SET {weight_col} = {dens_col} * {area_col}"
        )
    con.raw_sql(f"CREATE INDEX ON {qualified} USING GIST ({geom_col})")


def postprocess_point_output(
    con,
    table_name: str,
    geom_col: str,
    schema: str = "public",
):
    """Create the legacy point-stage spatial index when running on PostGIS."""
    backend = get_backend_name(con)
    if backend != "postgres":
        logger.info(
            "%s: backend '%s' - skipping PostGIS-only point index",
            table_name,
            backend,
        )
        return

    qualified = f"{schema}.{table_name}"
    con.raw_sql(f"CREATE INDEX ON {qualified} USING GIST ({geom_col})")


def build_polygon_wa_wp_cty_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 1 result as an ibis expression."""
    data_t = load_table_expr(con, job.data_table, schema).alias("data")
    weight_t = apply_filter_function(
        load_table_expr(con, job.weight_table, schema),
        job.filter_function,
        job.weight_table,
    ).alias("weight")
    wa = job.weight_attribute_column
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"
    dens_col = f"{wa}_dens_{srid}"
    area_col = f"area_{srid}"

    ensure_columns(data_t, job.data_table, [da, geom])
    ensure_columns(weight_t, job.weight_table, [dens_col, geom])
    ensure_geospatial_column(data_t, job.data_table, geom)
    ensure_geospatial_column(weight_t, job.weight_table, geom)

    if job.data_table == job.weight_table:
        clipped_geom = weight_t[geom]
        area_expr = clipped_geom.area()
        return weight_t.select(
            weight_t[da].name(da),
            (weight_t[dens_col] * area_expr).name(wa),
            weight_t[dens_col].name(dens_col),
            area_expr.name(area_col),
            clipped_geom.name(geom),
        )

    overlap = (
        weight_t[geom].intersects(data_t[geom])
        & ~weight_t[geom].touches(data_t[geom])
    )
    joined = data_t.join(weight_t, overlap)
    clipped_geom = ibis.ifelse(
        weight_t[geom].covered_by(data_t[geom]),
        weight_t[geom],
        weight_t[geom].intersection(data_t[geom]),
    )
    area_expr = clipped_geom.area()
    return joined.select(
        data_t[da].name(da),
        (weight_t[dens_col] * area_expr).name(wa),
        weight_t[dens_col].name(dens_col),
        area_expr.name(area_col),
        clipped_geom.name(geom),
    )


def build_polygon_wa_wp_cty_cell_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 2 result as an ibis expression."""
    wp_t = load_table_expr(con, job.wp_cty_table, schema).alias("wp")
    grid_t = load_table_expr(con, job.grid_name, schema).alias("g")
    wa = job.weight_attribute_column
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"
    dens_col = f"{wa}_dens_{srid}"
    area_col = f"area_{srid}"

    ensure_columns(wp_t, job.wp_cty_table, [da, dens_col, geom])
    ensure_columns(grid_t, job.grid_name, ["colnum", "rownum", "gridcell"])
    ensure_geospatial_column(wp_t, job.wp_cty_table, geom)
    ensure_geospatial_column(grid_t, job.grid_name, "gridcell")

    overlap = (
        wp_t[geom].intersects(grid_t.gridcell)
        & ~wp_t[geom].touches(grid_t.gridcell)
    )
    joined = wp_t.join(grid_t, overlap)
    clipped_geom = ibis.ifelse(
        wp_t[geom].covered_by(grid_t.gridcell),
        wp_t[geom],
        wp_t[geom].intersection(grid_t.gridcell),
    )
    area_expr = clipped_geom.area()
    return joined.select(
        wp_t[da].name(da),
        grid_t.colnum.name("colnum"),
        grid_t.rownum.name("rownum"),
        area_expr.name(area_col),
        (wp_t[dens_col] * area_expr).name(wa),
        wp_t[dens_col].name(dens_col),
        clipped_geom.name(geom),
    )


def build_polygon_no_wa_wp_cty_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 1 area-only polygon result as an ibis expression."""
    data_t = load_table_expr(con, job.data_table, schema).alias("data")
    weight_t = apply_filter_function(
        load_table_expr(con, job.weight_table, schema),
        job.filter_function,
        job.weight_table,
    ).alias("weight")
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"
    area_col = f"area_{srid}"

    ensure_columns(data_t, job.data_table, [da, geom])
    ensure_columns(weight_t, job.weight_table, [geom])
    ensure_geospatial_column(data_t, job.data_table, geom)
    ensure_geospatial_column(weight_t, job.weight_table, geom)

    if job.data_table == job.weight_table:
        clipped_geom = weight_t[geom]
        area_expr = clipped_geom.area()
        return weight_t.select(
            weight_t[da].name(da),
            area_expr.name(area_col),
            clipped_geom.name(geom),
        )

    overlap = (
        weight_t[geom].intersects(data_t[geom])
        & ~weight_t[geom].touches(data_t[geom])
    )
    joined = data_t.join(weight_t, overlap)
    clipped_geom = ibis.ifelse(
        weight_t[geom].covered_by(data_t[geom]),
        weight_t[geom],
        weight_t[geom].intersection(data_t[geom]),
    )
    area_expr = clipped_geom.area()
    return joined.select(
        data_t[da].name(da),
        area_expr.name(area_col),
        clipped_geom.name(geom),
    )


def build_polygon_no_wa_wp_cty_cell_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 2 area-only polygon result as an ibis expression."""
    wp_t = load_table_expr(con, job.wp_cty_table, schema).alias("wp")
    grid_t = load_table_expr(con, job.grid_name, schema).alias("g")
    da = job.data_attribute
    srid = job.srid
    geom = f"geom_{srid}"
    area_col = f"area_{srid}"

    ensure_columns(wp_t, job.wp_cty_table, [da, geom])
    ensure_columns(grid_t, job.grid_name, ["colnum", "rownum", "gridcell"])
    ensure_geospatial_column(wp_t, job.wp_cty_table, geom)
    ensure_geospatial_column(grid_t, job.grid_name, "gridcell")

    overlap = (
        wp_t[geom].intersects(grid_t.gridcell)
        & ~wp_t[geom].touches(grid_t.gridcell)
    )
    joined = wp_t.join(grid_t, overlap)
    clipped_geom = ibis.ifelse(
        wp_t[geom].covered_by(grid_t.gridcell),
        wp_t[geom],
        wp_t[geom].intersection(grid_t.gridcell),
    )
    area_expr = clipped_geom.area()
    return joined.select(
        wp_t[da].name(da),
        grid_t.colnum.name("colnum"),
        grid_t.rownum.name("rownum"),
        area_expr.name(area_col),
        clipped_geom.name(geom),
    )


def build_point_no_wa_wp_cty_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 1 point-count result as an ibis expression."""
    data_t = load_table_expr(con, job.data_table, schema).alias("data")
    weight_t = apply_filter_function(
        load_table_expr(con, job.weight_table, schema),
        job.filter_function,
        job.weight_table,
    ).alias("weight")
    da = job.data_attribute
    geom = f"geom_{job.srid}"

    ensure_columns(data_t, job.data_table, [da, geom])
    ensure_columns(weight_t, job.weight_table, [geom])
    ensure_geospatial_column(data_t, job.data_table, geom)
    ensure_geospatial_column(weight_t, job.weight_table, geom)

    joined = weight_t.join(data_t, data_t[geom].contains(weight_t[geom]))
    return joined.select(
        data_t[da].name(da),
        ibis.literal(1).cast("int32").name("count_wp_cty"),
        weight_t[geom].name(geom),
    )


def build_point_no_wa_wp_cty_cell_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 2 point-count result as an ibis expression."""
    wp_t = load_table_expr(con, job.wp_cty_table, schema).alias("wp")
    grid_t = load_table_expr(con, job.grid_name, schema).alias("g")
    da = job.data_attribute
    geom = f"geom_{job.srid}"

    ensure_columns(wp_t, job.wp_cty_table, [da, geom])
    ensure_columns(grid_t, job.grid_name, ["colnum", "rownum", "gridcell"])
    ensure_geospatial_column(wp_t, job.wp_cty_table, geom)
    ensure_geospatial_column(grid_t, job.grid_name, "gridcell")

    joined = wp_t.join(grid_t, grid_t.gridcell.contains(wp_t[geom]))
    return joined.select(
        wp_t[da].name(da),
        grid_t.colnum.name("colnum"),
        grid_t.rownum.name("rownum"),
        ibis.literal(1).cast("int32").name("count_wp_cty_cell"),
        wp_t[geom].name(geom),
    )


def create_wp_cty(con, job: SurrogateJob, schema: str = "public"):
    """Stage 1: intersect weight geometries with data boundaries.

    Current scope supports polygon geometry with or without a weight
    attribute, plus point geometry without a weight attribute.
    """
    if job.geom_family == "polygon":
        if job.has_weight_attr:
            _create_polygon_wa_wp_cty(con, job, schema)
            return

        _create_polygon_no_wa_wp_cty(con, job, schema)
        return

    if job.geom_family == "point":
        if job.has_weight_attr:
            raise NotImplementedError(
                "point geometry with weight attribute is not yet supported "
                "in create_wp_cty"
            )

        _create_point_no_wa_wp_cty(con, job, schema)
        return

    raise NotImplementedError(
        f"geom_family='{job.geom_family}' not yet supported in create_wp_cty"
    )


def _create_polygon_wa_wp_cty(con, job: SurrogateJob, schema: str):
    """Polygon + weight-attribute path for Stage 1."""
    wa = job.weight_attribute_column
    srid = job.srid
    geom = f"geom_{srid}"

    expr = build_polygon_wa_wp_cty_expr(con, job, schema)
    materialize_table(con, job.wp_cty_table, expr, schema)
    postprocess_polygon_output(con, job.wp_cty_table, geom, wa, srid, schema)


def _create_polygon_no_wa_wp_cty(con, job: SurrogateJob, schema: str):
    """Polygon + no-weight-attribute path for Stage 1."""
    srid = job.srid
    geom = f"geom_{srid}"

    expr = build_polygon_no_wa_wp_cty_expr(con, job, schema)
    materialize_table(con, job.wp_cty_table, expr, schema)
    postprocess_polygon_output(con, job.wp_cty_table, geom, None, srid, schema)


def _create_point_no_wa_wp_cty(con, job: SurrogateJob, schema: str):
    """Point + no-weight-attribute path for Stage 1."""
    geom = f"geom_{job.srid}"

    expr = build_point_no_wa_wp_cty_expr(con, job, schema)
    materialize_table(con, job.wp_cty_table, expr, schema)
    postprocess_point_output(con, job.wp_cty_table, geom, schema)


# ---------------------------------------------------------------------------
# Stage 2: wp_cty_cell — intersect wp_cty with grid cells
# ---------------------------------------------------------------------------
#
# Purpose: overlay Stage 1 results onto the modeling grid.  Same clipping
# pattern as Stage 1 (ST_CoveredBy fast path, then ST_Intersection).
# After clipping to grid cells, area and weight are recomputed again.

def create_wp_cty_cell(con, job: SurrogateJob, schema: str = "public"):
    """Stage 2: intersect wp_cty with grid cells.

    Current scope supports polygon geometry with or without a weight
    attribute, plus point geometry without a weight attribute.
    """
    if job.geom_family == "polygon":
        if job.has_weight_attr:
            _create_polygon_wa_wp_cty_cell(con, job, schema)
            return

        _create_polygon_no_wa_wp_cty_cell(con, job, schema)
        return

    if job.geom_family == "point":
        if job.has_weight_attr:
            raise NotImplementedError(
                "point geometry with weight attribute is not yet supported "
                "in create_wp_cty_cell"
            )

        _create_point_no_wa_wp_cty_cell(con, job, schema)
        return

    raise NotImplementedError(
        f"geom_family='{job.geom_family}' not yet supported in "
        "create_wp_cty_cell"
    )


def _create_polygon_wa_wp_cty_cell(con, job: SurrogateJob, schema: str):
    """Polygon + weight-attribute path for Stage 2."""
    wa = job.weight_attribute_column
    srid = job.srid
    geom = f"geom_{srid}"

    expr = build_polygon_wa_wp_cty_cell_expr(con, job, schema)
    materialize_table(con, job.wp_cty_cell_table, expr, schema)
    postprocess_polygon_output(
        con, job.wp_cty_cell_table, geom, wa, srid, schema
    )


def _create_polygon_no_wa_wp_cty_cell(con, job: SurrogateJob, schema: str):
    """Polygon + no-weight-attribute path for Stage 2."""
    srid = job.srid
    geom = f"geom_{srid}"

    expr = build_polygon_no_wa_wp_cty_cell_expr(con, job, schema)
    materialize_table(con, job.wp_cty_cell_table, expr, schema)
    postprocess_polygon_output(
        con, job.wp_cty_cell_table, geom, None, srid, schema
    )


def _create_point_no_wa_wp_cty_cell(con, job: SurrogateJob, schema: str):
    """Point + no-weight-attribute path for Stage 2."""
    geom = f"geom_{job.srid}"

    expr = build_point_no_wa_wp_cty_cell_expr(con, job, schema)
    materialize_table(con, job.wp_cty_cell_table, expr, schema)
    postprocess_point_output(con, job.wp_cty_cell_table, geom, schema)


# ---------------------------------------------------------------------------
# Stage 3: numer — aggregate Stage 2 values to grid cells per data unit
# ---------------------------------------------------------------------------
#
# Purpose: summarize the Stage 2 overlay into one row per
# (data_attribute, colnum, rownum), matching legacy `numer_*`.
#
# This stage is relational only: it should not depend on backend-specific
# geometry repair or index creation.

def build_numer_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 3 numerator result as an ibis expression."""
    cell_t = load_table_expr(con, job.wp_cty_cell_table, schema)
    da = job.data_attribute
    value_col = get_numer_measure_column(job)

    ensure_columns(
        cell_t,
        job.wp_cty_cell_table,
        [da, "colnum", "rownum", value_col],
    )

    numer_t = cell_t.group_by([da, "colnum", "rownum"]).aggregate(
        numer=cell_t[value_col].sum()
    )
    return numer_t.select(da, "colnum", "rownum", "numer")


def create_numer(con, job: SurrogateJob, schema: str = "public"):
    """Stage 3: aggregate Stage 2 rows into grid-cell numerators."""
    expr = build_numer_expr(con, job, schema)
    materialize_table(con, job.numer_table, expr, schema)


# ---------------------------------------------------------------------------
# Stage 4: denom — aggregate Stage 1 values per data unit
# ---------------------------------------------------------------------------
#
# Purpose: summarize Stage 1 into one row per data unit, matching legacy
# `denom_*` for the currently supported polygon branches.
#
# Future geometry may need a different source table or measure
# expression; current scope keeps this on `wp_cty_*`.

def build_denom_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 4 denominator result as an ibis expression."""
    wp_t = load_table_expr(con, job.wp_cty_table, schema)
    da = job.data_attribute
    value_col = get_denom_measure_column(job)

    ensure_columns(
        wp_t,
        job.wp_cty_table,
        [da, value_col],
    )

    denom_t = wp_t.group_by([da]).aggregate(
        denom=wp_t[value_col].sum()
    )
    return denom_t.select(da, "denom")


def create_denom(con, job: SurrogateJob, schema: str = "public"):
    """Stage 4: aggregate Stage 1 rows into per-data-unit denominators."""
    expr = build_denom_expr(con, job, schema)
    materialize_table(con, job.denom_table, expr, schema)


# ---------------------------------------------------------------------------
# Stage 5: surg - join numerators to denominators and compute ratios
# ---------------------------------------------------------------------------
#
# Purpose: produce final surrogate ratios from the Stage 3/4 relational
# outputs. This stage only needs numer and denom tables with the expected columns from Stage 3/4.


def build_surg_expr(con, job: SurrogateJob, schema: str):
    """Build the Stage 5 surrogate ratio result as an ibis expression."""
    numer_t = load_table_expr(con, job.numer_table, schema).alias("n")
    denom_t = load_table_expr(con, job.denom_table, schema).alias("d")
    da = job.data_attribute

    ensure_columns(
        numer_t,
        job.numer_table,
        [da, "colnum", "rownum", "numer"],
    )
    ensure_columns(
        denom_t,
        job.denom_table,
        [da, "denom"],
    )

    joined = numer_t.join(denom_t, numer_t[da] == denom_t[da])
    return joined.filter(
        (numer_t["numer"] != 0) & (denom_t["denom"] != 0)
    ).select(
        ibis.literal(job.surrogate_code).cast("int32").name("surg_code"),
        numer_t[da].name(da),
        numer_t["colnum"].name("colnum"),
        numer_t["rownum"].name("rownum"),
        (numer_t["numer"] / denom_t["denom"]).name("surg"),
        numer_t["numer"].name("numer"),
        denom_t["denom"].name("denom"),
    )


def create_surg(con, job: SurrogateJob, schema: str = "public"):
    """Stage 5: join Stage 3/4 outputs and compute final surrogate ratios."""
    expr = build_surg_expr(con, job, schema)
    materialize_table(con, job.surg_table, expr, schema)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def compute_surrogate(con, job: SurrogateJob, schema: str = "public") -> dict:
    """Run available stages for one surrogate job.

    Currently runs Stage 1 (wp_cty), Stage 2 (wp_cty_cell),
    Stage 3 (numer), Stage 4 (denom), and Stage 5 (surg).
    File export is not yet implemented.
    """
    start = time.time()
    logger.info("Computing surrogate %d (%s)...",
                job.surrogate_code, job.surrogate_name)

    try:
        # Stage 1: weight × data intersection
        logger.info("  Stage 1: create_wp_cty (weight-data intersection)")
        t1 = time.time()
        create_wp_cty(con, job, schema)
        cnt1 = get_table_row_count(con, job.wp_cty_table, schema)
        logger.info("  Stage 1 complete: %s (%d rows, %.1fs)",
                     job.wp_cty_table, cnt1, time.time() - t1)

        # Stage 2: grid cell intersection
        logger.info("  Stage 2: create_wp_cty_cell (grid intersection)")
        t2 = time.time()
        create_wp_cty_cell(con, job, schema)
        cnt2 = get_table_row_count(con, job.wp_cty_cell_table, schema)
        logger.info("  Stage 2 complete: %s (%d rows, %.1fs)",
                     job.wp_cty_cell_table, cnt2, time.time() - t2)

        # Stage 3: numerator aggregation
        logger.info("  Stage 3: create_numer (grid-cell numerator aggregation)")
        t3 = time.time()
        create_numer(con, job, schema)
        cnt3 = get_table_row_count(con, job.numer_table, schema)
        logger.info("  Stage 3 complete: %s (%d rows, %.1fs)",
                    job.numer_table, cnt3, time.time() - t3)

        # Stage 4: denominator aggregation
        logger.info("  Stage 4: create_denom (data-unit denominator aggregation)")
        t4 = time.time()
        create_denom(con, job, schema)
        cnt4 = get_table_row_count(con, job.denom_table, schema)
        logger.info("  Stage 4 complete: %s (%d rows, %.1fs)",
                    job.denom_table, cnt4, time.time() - t4)

        # Stage 5: final surrogate ratios
        logger.info("  Stage 5: create_surg (final numer/denom ratio)")
        t5 = time.time()
        create_surg(con, job, schema)
        cnt5 = get_table_row_count(con, job.surg_table, schema)
        logger.info("  Stage 5 complete: %s (%d rows, %.1fs)",
                    job.surg_table, cnt5, time.time() - t5)

        elapsed = round(time.time() - start, 1)
        logger.info("Surrogate %d done (%.1fs)", job.surrogate_code, elapsed)
        return {
            "code": job.surrogate_code,
            "name": job.surrogate_name,
            "status": "success",
            "wp_cty_rows": cnt1,
            "wp_cty_cell_rows": cnt2,
            "numer_rows": cnt3,
            "denom_rows": cnt4,
            "surg_rows": cnt5,
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
              f"wp_cty_cell={r['wp_cty_cell_rows']} "
              f"numer={r['numer_rows']} "
              f"denom={r['denom_rows']} "
              f"surg={r['surg_rows']} ({r['elapsed']}s)")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['code']} ({r['name']}): {r.get('reason', '')}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------

def parse_yes_no(value: str, default: bool = False) -> bool:
    """Parse a legacy YES/NO control value."""
    if not value:
        return default
    return value.strip().upper() == "YES"


def get_projection_header(coordtype: int) -> tuple[str, str]:
    """Map an IOAPI coordinate type to legacy file header labels."""
    mapping = {
        1: ("LAT-LON", "degrees"),
        2: ("LAMBERT", "meters"),
        5: ("UTM", "meters"),
    }
    if coordtype not in mapping:
        raise ValueError(f"unsupported output coordtype {coordtype}")
    return mapping[coordtype]


def build_grid_header(grid_name: str, griddesc_path: Path) -> str:
    """Build the legacy #GRID header line for one output grid."""
    coord_systems, grids = generate_grid.parse_griddesc(griddesc_path)
    if grid_name not in grids:
        raise ValueError(f"{griddesc_path}: grid {grid_name!r} not found")

    grid_def = grids[grid_name]
    coord_name = grid_def["coord_name"]
    if coord_name not in coord_systems:
        raise ValueError(
            f"{griddesc_path}: coord system {coord_name!r} not found for "
            f"grid {grid_name!r}"
        )

    coord = coord_systems[coord_name]
    proj_name, units = get_projection_header(coord["coordtype"])
    parts = [
        "#GRID",
        grid_name,
        f"{grid_def['xorig']:.6f}",
        f"{grid_def['yorig']:.6f}",
        f"{grid_def['xcell']:.6f}",
        f"{grid_def['ycell']:.6f}",
        str(grid_def["ncols"]),
        str(grid_def["nrows"]),
        str(grid_def["nthik"]),
        proj_name,
        units,
        f"{coord['p_alp']:.6f}",
        f"{coord['p_bet']:.6f}",
        f"{coord['p_gam']:.6f}",
        f"{coord['xcent']:.6f}",
        f"{coord['ycent']:.6f}",
    ]
    return "\t".join(parts)


def build_output_config(
    controls: dict[str, str],
    control_path: Path,
) -> OutputConfig:
    """Create run-level output settings from control variables."""
    grid_name = controls.get("OUTPUT_GRID_NAME", "").strip()
    griddesc_value = controls.get("GRIDDESC", "").strip()
    if not grid_name:
        raise ValueError("OUTPUT_GRID_NAME is required for file export")
    if not griddesc_value:
        raise ValueError("GRIDDESC is required for file export")

    griddesc_path = Path(griddesc_value)
    srgdesc_value = controls.get("OUTPUT SRGDESC FILE", "").strip()
    total_value = controls.get("OUTPUT SURROGATE FILE", "").strip()

    return OutputConfig(
        grid_header=build_grid_header(grid_name, griddesc_path),
        control_file_label=str(control_path),
        generation_file_label=controls.get("GENERATION CONTROL FILE", ""),
        specification_file_label=controls.get("SURROGATE SPECIFICATION FILE", ""),
        shapefile_catalog_label=controls.get("SHAPEFILE CATALOG", ""),
        surrogate_code_file_label=controls.get("SURROGATE CODE FILE", ""),
        griddesc_label=griddesc_value,
        srgdesc_path=(
            None if not srgdesc_value or srgdesc_value.upper() == "NONE"
            else Path(srgdesc_value)
        ),
        overwrite_output_files=parse_yes_no(
            controls.get("OVERWRITE OUTPUT FILES", "YES"),
            default=True,
        ),
        combined_output_path=(
            None if not total_value or total_value.upper() == "NONE"
            else Path(total_value)
        ),
        cleanup_intermediate_tables=parse_yes_no(
            controls.get("CLEANUP INTERMEDIATE TABLES", "NO"),
            default=False,
        ),
    )


def build_nofill_rows_expr(con, job: SurrogateJob, schema: str):
    """Build the ordered NOFILL output rows from the Stage 5 table."""
    surg_t = load_table_expr(con, job.surg_table, schema)
    da = job.data_attribute

    ensure_columns(
        surg_t,
        job.surg_table,
        ["surg_code", da, "colnum", "rownum", "surg", "numer", "denom"],
    )

    return (
        surg_t.select(
            surg_t["surg_code"].name("surg_code"),
            surg_t[da].name(da),
            surg_t["colnum"].name("colnum"),
            surg_t["rownum"].name("rownum"),
            surg_t["surg"].round(10).name("surg"),
            ibis.literal("!").name("sep"),
            surg_t["numer"].name("numer"),
            surg_t["denom"].name("denom"),
        )
        .order_by([da, "colnum", "rownum"])
    )


def iter_expr_rows(con, expr, chunk_size: int = 10000):
    """Yield rows from an ibis expression, preferring streaming SQL access."""
    try:
        cursor = con.raw_sql(con.compile(expr))
    except Exception:
        frame = expr.execute()
        for row in frame.itertuples(index=False, name=None):
            yield row
        return

    try:
        fetchmany = getattr(cursor, "fetchmany", None)
        if not callable(fetchmany):
            for row in cursor.fetchall():
                yield row
            return

        while True:
            rows = fetchmany(chunk_size)
            if not rows:
                break
            for row in rows:
                yield row
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def to_decimal(value: Any) -> Decimal:
    """Convert a numeric value into Decimal without scientific notation."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def format_fixed_number(value: Any, scale: int) -> str:
    """Render a numeric value for the surrogate result with fixed-point rounding and no exponent."""
    quant = Decimal(1).scaleb(-scale)
    text = format(
        to_decimal(value).quantize(quant, rounding=ROUND_HALF_UP),
        "f",
    )
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_plain_number(value: Any) -> str:
    """Render a numeric value in plain decimal for denon and numerform without scientific notation."""
    text = format(to_decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_nofill_row(row: tuple[Any, ...]) -> str:
    """Format one NOFILL row with legacy-compatible numeric text output."""
    surg_code, region_id, colnum, rownum, surg, sep, numer, denom = row
    fields = [
        str(surg_code),
        str(region_id),
        str(colnum),
        str(rownum),
        format_fixed_number(surg, 10),
        str(sep),
        format_plain_number(numer),
        format_plain_number(denom),
    ]
    return "\t".join(fields)


def build_nofill_header_lines(job: SurrogateJob, output_cfg: OutputConfig) -> list[str]:
    """Build the legacy-style NOFILL header block."""
    return [
        output_cfg.grid_header,
        f"#SRGDESC={job.surrogate_code},{job.surrogate_name}",
        "#",
        f"#SURROGATE REGION = {job.region}",
        f"#SURROGATE CODE = {job.surrogate_code}",
        f"#SURROGATE NAME = {job.surrogate_name}",
        f"#DATA SHAPEFILE = {job.data_table}",
        f"#DATA ATTRIBUTE = {job.data_attribute}",
        f"#WEIGHT SHAPEFILE = {job.weight_table}",
        f"#WEIGHT ATTRIBUTE = {job.weight_attribute}",
        f"#WEIGHT FUNCTION = {job.weight_function}",
        f"#FILTER FUNCTION = {job.filter_function}",
        "#",
        f"#CONTROL VARIABLE FILE = {output_cfg.control_file_label}",
        f"#SURROGATE SPECIFICATION FILE = {output_cfg.specification_file_label}",
        f"#SHAPEFILE CATALOG = {output_cfg.shapefile_catalog_label}",
        f"#GENERATION CONTROL FILE = {output_cfg.generation_file_label}",
        f"#SURROGATE CODE FILE = {output_cfg.surrogate_code_file_label}",
        f"#GRIDDESC = {output_cfg.griddesc_label}",
        "#",
        f"#USER = {getpass.getuser()}",
        f"#COMPUTER SYSTEM = {platform.system()}",
        f"#DATE = {time.strftime('%a %b %d %H:%M:%S %Z %Y')}",
    ]


def prepare_output_path(path: Path, overwrite: bool):
    """Create parent directories and enforce overwrite behavior."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"{path} is a directory")
    if path.exists() and not overwrite:
        raise FileExistsError(
            f"{path} already exists and OVERWRITE OUTPUT FILES=NO"
        )


def write_nofill(
    con,
    job: SurrogateJob,
    output_cfg: OutputConfig,
    schema: str = "public",
) -> SurrogateOutput:
    """Write one legacy-style NOFILL file from the Stage 5 table."""
    path = job.nofill_path
    prepare_output_path(path, output_cfg.overwrite_output_files)

    expr = build_nofill_rows_expr(con, job, schema)
    logger.info("  Output: write_nofill -> %s", path)

    with path.open("w", encoding="utf-8", newline="\n") as f:
        for line in build_nofill_header_lines(job, output_cfg):
            f.write(line + "\n")
        for row in iter_expr_rows(con, expr):
            f.write(format_nofill_row(row) + "\n")

    return SurrogateOutput(
        region=job.region,
        surrogate_code=job.surrogate_code,
        surrogate_name=job.surrogate_name,
        nofill_path=path,
    )


def read_existing_srgdesc_rows(path: Path) -> dict[str, tuple[str, str, str, str]]:
    """Read existing SRGDESC rows, ignoring headers/comments."""
    rows: dict[str, tuple[str, str, str, str]] = {}
    if not path.exists():
        return rows

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("#"):
                continue
            if len(row) != 4:
                logger.warning(
                    "Skipping malformed SRGDESC line in %s: %s",
                    path,
                    row,
                )
                continue
            key = f"{row[0]}_{row[1]}"
            rows[key] = (row[0], row[1], row[2], row[3])
    return rows


def write_srgdesc(outputs: list[SurrogateOutput], output_cfg: OutputConfig):
    """Write or refresh the run-level SRGDESC file."""
    if output_cfg.srgdesc_path is None:
        return
    if not outputs:
        return

    path = output_cfg.srgdesc_path
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = read_existing_srgdesc_rows(path)
    for output in outputs:
        key = f"{output.region}_{output.surrogate_code}"
        rows[key] = (
            output.region,
            str(output.surrogate_code),
            output.surrogate_name,
            output.nofill_path.as_posix(),
        )

    logger.info("Writing SRGDESC file -> %s", path)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(output_cfg.grid_header + "\n")
        writer = csv.writer(f, lineterminator="\n")
        for key in sorted(rows):
            writer.writerow(rows[key])


def cleanup_job_intermediate_tables(
    con,
    job: SurrogateJob,
    output_cfg: OutputConfig,
    schema: str = "public",
) -> dict[str, list[str]] | None:
    """Drop the current job's intermediate tables when cleanup is enabled."""
    if not output_cfg.cleanup_intermediate_tables:
        return None

    logger.info(
        "  Cleanup: drop intermediate tables for code=%s grid=%s srid=%s",
        job.surrogate_code,
        job.grid_name,
        job.srid,
    )
    return surrogate_utils.cleanup_intermediate_tables(
        con,
        surrogate_code=job.surrogate_code,
        srid=job.srid,
        grid_name=job.grid_name,
        schema=schema,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Compute spatial surrogates (Stage 1-5)."
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
            print(f"    nofill: {j.nofill_path}")
            if j.has_filter:
                print(f"    filter: {j.filter_function}")
            print()
        sys.exit(0)

    # ---- Connect and compute ----

    output_cfg = build_output_config(controls, control_path)
    config = db_utils.load_config(args.config)
    con = db_utils.connect_db(config)

    try:
        results = []
        written_outputs: list[SurrogateOutput] = []
        for job in jobs:
            result = compute_surrogate(con, job, args.schema)
            if result["status"] == "success":
                try:
                    written = write_nofill(con, job, output_cfg, args.schema)
                    cleanup_job_intermediate_tables(
                        con,
                        job,
                        output_cfg,
                        args.schema,
                    )
                    written_outputs.append(written)
                    result["nofill_path"] = written.nofill_path.as_posix()
                except Exception as exc:
                    logger.error(
                        "Surrogate %d output export or cleanup failed: %s",
                        job.surrogate_code,
                        exc,
                    )
                    result = {
                        "code": job.surrogate_code,
                        "name": job.surrogate_name,
                        "status": "error",
                        "reason": f"output export or cleanup failed: {exc}",
                    }
            results.append(result)

        if output_cfg.combined_output_path is not None:
            logger.info(
                "OUTPUT SURROGATE FILE is configured (%s) but not yet "
                "implemented in the Python rebuild",
                output_cfg.combined_output_path,
            )

        try:
            write_srgdesc(written_outputs, output_cfg)
        except Exception as exc:
            logger.error("SRGDESC export failed: %s", exc)
            results.append({
                "code": "SRGDESC",
                "name": "SRGDESC export",
                "status": "error",
                "reason": str(exc),
            })

        print_summary(results)
    finally:
        con.disconnect()

    any_failed = any(r["status"] == "error" for r in results)
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
