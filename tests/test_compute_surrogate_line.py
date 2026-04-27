import ibis
import ibis.expr.datatypes as dt

import compute_surrogate


def make_line_ff_no_wa_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=240,
        surrogate_name="Total Road Miles",
        data_table="roads",
        data_attribute="fips",
        weight_table="roads",
        weight_attribute="",
        weight_function="",
        filter_function="moves2014 > 1 and moves2014 < 6",
        weight_geomtype="MultiLineString",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_line_ff_wa_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=239,
        surrogate_name="Total Road AADT",
        data_table="roads",
        data_attribute="fips",
        weight_table="roads",
        weight_attribute="aadt",
        weight_function="",
        filter_function="moves2014 > 1 and moves2014 < 6",
        weight_geomtype="MultiLineString",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_table_map(job):
    geom = f"geom_{job.srid}"
    weight_columns = {
        job.data_attribute: "string",
        "moves2014": "int32",
        geom: dt.geometry,
    }
    wp_cty_columns = {
        job.data_attribute: "string",
        "length_wp_cty": "float64",
        geom: dt.geometry,
    }
    wp_cty_cell_columns = {
        job.data_attribute: "string",
        "colnum": "int32",
        "rownum": "int32",
        "length_wp_cty_cell": "float64",
        geom: dt.geometry,
    }
    if job.has_weight_attr:
        dens_col = f"{job.weight_attribute_column}_dens_{job.srid}"
        weight_columns[dens_col] = "float64"
        wp_cty_columns[dens_col] = "float64"
        wp_cty_columns["weighted_length_wp_cty"] = "float64"
        wp_cty_cell_columns[dens_col] = "float64"
        wp_cty_cell_columns["weighted_length_wp_cty_cell"] = "float64"

    return {
        job.weight_table: ibis.table(
            ibis.schema(weight_columns),
            name=job.weight_table,
        ),
        job.wp_cty_table: ibis.table(
            ibis.schema(wp_cty_columns),
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
            ibis.schema(wp_cty_cell_columns),
            name=job.wp_cty_cell_table,
        ),
    }


def patch_load_table_expr(monkeypatch, table_map):
    monkeypatch.setattr(
        compute_surrogate,
        "load_table_expr",
        lambda con, table_name, schema: table_map[table_name],
    )


class FakePostgresConnection:
    name = "postgres"

    def __init__(self):
        self.sql = []

    def raw_sql(self, statement):
        self.sql.append(statement)


def test_build_line_no_wa_wp_cty_expr_applies_filter_and_returns_expected_columns(
    monkeypatch,
):
    job = make_line_ff_no_wa_job()
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

    expr = compute_surrogate.build_line_no_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert filter_calls == [(job.filter_function, job.weight_table)]
    assert expr.schema().names == (
        job.data_attribute,
        "length_wp_cty",
        f"geom_{job.srid}",
    )


def test_build_line_no_wa_wp_cty_cell_expr_returns_expected_columns(monkeypatch):
    job = make_line_ff_no_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_line_no_wa_wp_cty_cell_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "length_wp_cty_cell",
        f"geom_{job.srid}",
    )


def test_build_line_wa_wp_cty_expr_applies_filter_and_returns_expected_columns(
    monkeypatch,
):
    job = make_line_ff_wa_job()
    table_map = make_table_map(job)
    filter_calls = []
    dens_col = f"{job.weight_attribute}_dens_{job.srid}"

    patch_load_table_expr(monkeypatch, table_map)
    monkeypatch.setattr(
        compute_surrogate,
        "apply_filter_function",
        lambda table_expr, filter_sql, table_name: (
            filter_calls.append((filter_sql, table_name)) or table_expr
        ),
    )

    expr = compute_surrogate.build_line_wa_wp_cty_expr(
        object(),
        job,
        "public",
    )

    assert filter_calls == [(job.filter_function, job.weight_table)]
    assert expr.schema().names == (
        job.data_attribute,
        dens_col,
        "length_wp_cty",
        "weighted_length_wp_cty",
        f"geom_{job.srid}",
    )


def test_build_line_wa_wp_cty_cell_expr_returns_expected_columns(monkeypatch):
    job = make_line_ff_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)
    dens_col = f"{job.weight_attribute}_dens_{job.srid}"

    expr = compute_surrogate.build_line_wa_wp_cty_cell_expr(
        object(),
        job,
        "public",
    )

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        dens_col,
        "length_wp_cty_cell",
        "weighted_length_wp_cty_cell",
        f"geom_{job.srid}",
    )


def test_postprocess_line_output_refreshes_weighted_length_after_length():
    con = FakePostgresConnection()

    compute_surrogate.postprocess_line_output(
        con,
        "wp_cty_239_900921",
        "geom_900921",
        "length_wp_cty",
        900921,
        "public",
        weighted_length_col="weighted_length_wp_cty",
        density_col="aadt_dens_900921",
    )

    assert any(
        "SET weighted_length_wp_cty = aadt_dens_900921 * length_wp_cty" in sql
        for sql in con.sql
    )


def test_build_numer_expr_uses_line_length_wp_cty_cell(monkeypatch):
    job = make_line_ff_no_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_numer_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "numer",
    )


def test_build_denom_expr_uses_line_length_wp_cty(monkeypatch):
    job = make_line_ff_no_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_denom_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "denom",
    )


def test_build_numer_expr_uses_line_weighted_length_wp_cty_cell(monkeypatch):
    job = make_line_ff_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_numer_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "colnum",
        "rownum",
        "numer",
    )


def test_build_denom_expr_uses_line_weighted_length_wp_cty(monkeypatch):
    job = make_line_ff_wa_job()
    table_map = make_table_map(job)
    patch_load_table_expr(monkeypatch, table_map)

    expr = compute_surrogate.build_denom_expr(object(), job, "public")

    assert expr.schema().names == (
        job.data_attribute,
        "denom",
    )


def test_create_wp_cty_routes_line_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_line_no_wa_wp_cty",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_line_ff_no_wa_job()
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 240, "custom")]


def test_create_wp_cty_routes_line_filter_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_line_wa_wp_cty",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_line_ff_wa_job()
    compute_surrogate.create_wp_cty(con, job, schema="custom")

    assert calls == [(con, 239, "custom")]


def test_create_wp_cty_cell_routes_line_filter_no_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_line_no_wa_wp_cty_cell",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_line_ff_no_wa_job()
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 240, "custom")]


def test_create_wp_cty_cell_routes_line_filter_weight_attr(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate,
        "_create_line_wa_wp_cty_cell",
        lambda con, job, schema: calls.append((con, job.surrogate_code, schema)),
    )

    con = object()
    job = make_line_ff_wa_job()
    compute_surrogate.create_wp_cty_cell(con, job, schema="custom")

    assert calls == [(con, 239, "custom")]
