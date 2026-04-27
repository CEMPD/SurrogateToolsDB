import ibis
import ibis.expr.datatypes as dt
import pytest

import compute_surrogate


def make_point_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=650,
        surrogate_name="Refineries and Tank Farms",
        data_table="counties",
        data_attribute="geoid",
        weight_table="weights",
        weight_attribute="NONE",
        weight_function="",
        filter_function="",
        weight_geomtype="MultiPoint",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_point_wa_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=508,
        surrogate_name="Public Schools",
        data_table="counties",
        data_attribute="geoid",
        weight_table="weights",
        weight_attribute="TOTAL",
        weight_function="",
        filter_function="",
        weight_geomtype="MultiPoint",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_point_ff_wa_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=205,
        surrogate_name="Extended Idle Locations",
        data_table="counties",
        data_attribute="geoid",
        weight_table="weights",
        weight_attribute="rev_truck",
        weight_function="",
        filter_function="rev_truck > 0",
        weight_geomtype="MultiPoint",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_table_map(job):
    geom = f"geom_{job.srid}"
    weight_schema = {geom: dt.geometry}
    wp_cty_schema = {
        job.data_attribute: "string",
        geom: dt.geometry,
    }
    wp_cty_cell_schema = {
        job.data_attribute: "string",
        "colnum": "int32",
        "rownum": "int32",
        geom: dt.geometry,
    }

    if job.has_weight_attr:
        weight_schema[job.weight_attribute_column] = "float64"
        wp_cty_schema[job.weight_attribute_column] = "float64"
        wp_cty_cell_schema[job.weight_attribute_column] = "float64"
    else:
        wp_cty_schema["count_wp_cty"] = "int32"
        wp_cty_cell_schema["count_wp_cty_cell"] = "int32"

    return {
        job.data_table: ibis.table(
            ibis.schema(
                {
                    job.data_attribute: "string",
                    geom: dt.geometry,
                }
            ),
            name=job.data_table,
        ),
        job.weight_table: ibis.table(
            ibis.schema(weight_schema),
            name=job.weight_table,
        ),
        job.wp_cty_table: ibis.table(
            ibis.schema(wp_cty_schema),
            name=job.wp_cty_table,
        ),
        job.grid_name: ibis.table(
            ibis.schema(
                {
                    "colnum": "int32",
                    "rownum": "int32",
                    "gridcell": dt.geometry,
                }
            ),
            name=job.grid_name,
        ),
        job.wp_cty_cell_table: ibis.table(
            ibis.schema(wp_cty_cell_schema),
            name=job.wp_cty_cell_table,
        ),
    }


def patch_load_table_expr(monkeypatch, table_map):
    monkeypatch.setattr(
        compute_surrogate,
        "load_table_expr",
        lambda con, table_name, schema: table_map[table_name],
    )


def test_build_point_no_wa_wp_cty_expr_returns_expected_columns(monkeypatch):
    job = make_point_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_point_no_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        "count_wp_cty",
        f"geom_{job.srid}",
    )


def test_build_point_no_wa_wp_cty_expr_applies_filter_function(monkeypatch):
    job = make_point_job()
    job.filter_function = "bus_t = 1"
    table_map = make_table_map(job)
    filter_calls = []

    patch_load_table_expr(monkeypatch, table_map)
    monkeypatch.setattr(
        compute_surrogate,
        "apply_filter_function",
        lambda table_expr, filter_sql, table_name: (
            filter_calls.append((filter_sql, table_name)) or table_expr
        ),
    )

    expr = compute_surrogate.build_point_no_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert filter_calls == [("bus_t = 1", job.weight_table)]
    assert expr.schema().names == (
        job.data_attribute,
        "count_wp_cty",
        f"geom_{job.srid}",
    )


def test_build_point_no_wa_wp_cty_cell_expr_returns_expected_columns(monkeypatch):
    job = make_point_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_point_no_wa_wp_cty_cell_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "count_wp_cty_cell",
        f"geom_{job.srid}",
    )


def test_build_point_wa_wp_cty_expr_returns_expected_columns(monkeypatch):
    job = make_point_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_point_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        job.weight_attribute,
        f"geom_{job.srid}",
    )


def test_build_point_wa_wp_cty_expr_applies_filter_function(monkeypatch):
    job = make_point_ff_wa_job()
    table_map = make_table_map(job)
    filter_calls = []

    patch_load_table_expr(monkeypatch, table_map)
    monkeypatch.setattr(
        compute_surrogate,
        "apply_filter_function",
        lambda table_expr, filter_sql, table_name: (
            filter_calls.append((filter_sql, table_name)) or table_expr
        ),
    )

    expr = compute_surrogate.build_point_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert filter_calls == [("rev_truck > 0", job.weight_table)]
    assert expr.schema().names == (
        job.data_attribute,
        job.weight_attribute,
        f"geom_{job.srid}",
    )


def test_build_point_wa_wp_cty_cell_expr_returns_expected_columns(monkeypatch):
    job = make_point_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_point_wa_wp_cty_cell_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        job.weight_attribute,
        f"geom_{job.srid}",
    )


def test_build_numer_expr_uses_point_count_wp_cty_cell(monkeypatch):
    job = make_point_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_numer_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "numer",
    )


def test_build_numer_expr_uses_point_count_wp_cty_cell_with_filter(monkeypatch):
    job = make_point_job()
    job.filter_function = "bus_t = 1"
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_numer_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "numer",
    )


def test_build_denom_expr_uses_point_count_wp_cty(monkeypatch):
    job = make_point_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_denom_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "denom",
    )


def test_build_denom_expr_uses_point_count_wp_cty_with_filter(monkeypatch):
    job = make_point_job()
    job.filter_function = "bus_t = 1"
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_denom_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "denom",
    )


def test_build_numer_expr_uses_point_weight_attribute(monkeypatch):
    job = make_point_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_numer_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "numer",
    )


def test_build_denom_expr_uses_point_weight_attribute(monkeypatch):
    job = make_point_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_denom_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "denom",
    )


def test_create_wp_cty_routes_point_no_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_no_wa_wp_cty",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_point_job()
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 650, "custom")]


def test_create_wp_cty_routes_point_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_no_wa_wp_cty",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.filter_function, schema)
        ),
    )

    con = object()
    job = make_point_job()
    job.filter_function = "bus_t = 1"
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 650, "bus_t = 1", "custom")]


def test_create_wp_cty_routes_point_no_filter_with_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_wa_wp_cty",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.weight_attribute, schema)
        ),
    )

    con = object()
    job = make_point_wa_job()
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 508, "TOTAL", "custom")]


def test_create_wp_cty_routes_point_filter_with_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_wa_wp_cty",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.filter_function, schema)
        ),
    )

    con = object()
    job = make_point_ff_wa_job()
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 205, "rev_truck > 0", "custom")]


def test_create_wp_cty_cell_routes_point_no_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_no_wa_wp_cty_cell",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_point_job()
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 650, "custom")]


def test_create_wp_cty_cell_routes_point_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_no_wa_wp_cty_cell",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.filter_function, schema)
        ),
    )

    con = object()
    job = make_point_job()
    job.filter_function = "bus_t = 1"
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 650, "bus_t = 1", "custom")]


def test_create_wp_cty_cell_routes_point_no_filter_with_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_wa_wp_cty_cell",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.weight_attribute, schema)
        ),
    )

    con = object()
    job = make_point_wa_job()
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 508, "TOTAL", "custom")]


def test_create_wp_cty_cell_routes_point_filter_with_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_point_wa_wp_cty_cell",
        lambda con, job, schema: calls.append(
            (con, job.surrogate_code, job.filter_function, schema)
        ),
    )

    con = object()
    job = make_point_ff_wa_job()
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 205, "rev_truck > 0", "custom")]
