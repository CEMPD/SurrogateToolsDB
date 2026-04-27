from pathlib import Path

import compute_surrogate


def make_job():
    return compute_surrogate.SurrogateJob(
        region="USA",
        surrogate_code=100,
        surrogate_name="Population",
        data_table="counties",
        data_attribute="geoid",
        weight_table="weights",
        weight_attribute="pop2016",
        weight_function="",
        filter_function="",
        weight_geomtype="MultiPolygon",
        grid_name="us12k_516x444",
        srid=900921,
        output_dir="./outputs",
        denominator_threshold=0.0005,
    )


def make_controls(cleanup_value="NO"):
    return {
        "OUTPUT_GRID_NAME": "us12k_516x444",
        "GRIDDESC": "GRIDDESC.txt",
        "OUTPUT SRGDESC FILE": "NONE",
        "OUTPUT SURROGATE FILE": "NONE",
        "OVERWRITE OUTPUT FILES": "YES",
        "GENERATION CONTROL FILE": "surrogate_generation_pg.quickstart.csv",
        "SURROGATE SPECIFICATION FILE": "surrogate_specification_pg.quickstart.csv",
        "SHAPEFILE CATALOG": "shapefile_catalog_pg.2017.csv",
        "SURROGATE CODE FILE": "surrogate_codes.2017.csv",
        "CLEANUP INTERMEDIATE TABLES": cleanup_value,
    }


def make_output_config(cleanup_enabled):
    return compute_surrogate.OutputConfig(
        grid_header="#GRID",
        control_file_label="control_variables_pg.quickstart.csv",
        generation_file_label="surrogate_generation_pg.quickstart.csv",
        specification_file_label="surrogate_specification_pg.quickstart.csv",
        shapefile_catalog_label="shapefile_catalog_pg.2017.csv",
        surrogate_code_file_label="surrogate_codes.2017.csv",
        griddesc_label="GRIDDESC.txt",
        srgdesc_path=None,
        overwrite_output_files=True,
        combined_output_path=None,
        cleanup_intermediate_tables=cleanup_enabled,
    )


def test_build_output_config_reads_cleanup_control_variable(monkeypatch):
    monkeypatch.setattr(
        compute_surrogate,
        "build_grid_header",
        lambda grid_name, griddesc_path: f"#GRID {grid_name} {griddesc_path}",
    )

    output_cfg = compute_surrogate.build_output_config(
        make_controls(cleanup_value="YES"),
        Path("control_variables_pg.quickstart.csv"),
    )

    assert output_cfg.cleanup_intermediate_tables is True


def test_surrogate_job_treats_legacy_none_weight_attribute_as_absent():
    job = make_job()
    job.weight_attribute = "NONE"

    assert job.has_weight_attr is False
    assert compute_surrogate.get_effective_measure_column(job) == "area_900921"


def test_surrogate_job_weight_attribute_column_rejects_legacy_none():
    job = make_job()
    job.weight_attribute = "NONE"

    try:
        job.weight_attribute_column
    except ValueError as exc:
        assert "NONE/blank" in str(exc)
    else:
        raise AssertionError("expected legacy NONE weight attribute to raise")


def test_build_jobs_preserves_legacy_none_metadata_but_uses_no_weight_attr_branch():
    controls = {
        "OUTPUT_GRID_NAME": "us12k_516x444",
        "SRID_FINAL": "900921",
        "OUTPUT DIRECTORY": "./outputs",
        "DENOMINATOR_THRESHOLD": "0.0005",
    }
    generations = [
        {
            "REGION": "USA",
            "SURROGATE CODE": "305",
            "SURROGATE": "NLCD Low + Med",
        }
    ]
    specifications = {
        "USA_305": {
            "DATA ATTRIBUTE": "geoid",
            "DATA SHAPEFILE": "cb_2017_us_county_500k",
            "WEIGHT SHAPEFILE": "CONUS_AK_NLCD_2011_500m_WGS",
            "WEIGHT ATTRIBUTE": "NONE",
            "WEIGHT FUNCTION": "",
            "FILTER FUNCTION": "gridcode IN (22,23)",
        }
    }
    catalog = {
        "conus_ak_nlcd_2011_500m_wgs": {
            "geomtype": "MultiPolygon",
        }
    }

    jobs = compute_surrogate.build_jobs(
        controls,
        generations,
        specifications,
        catalog,
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.weight_attribute == "NONE"
    assert job.has_weight_attr is False
    assert compute_surrogate.get_effective_measure_column(job) == "area_900921"


def test_cleanup_job_intermediate_tables_skips_when_disabled(monkeypatch):
    calls = []

    monkeypatch.setattr(
        compute_surrogate.surrogate_utils,
        "cleanup_intermediate_tables",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = compute_surrogate.cleanup_job_intermediate_tables(
        object(),
        make_job(),
        make_output_config(cleanup_enabled=False),
        schema="public",
    )

    assert result is None
    assert calls == []


def test_cleanup_job_intermediate_tables_calls_shared_helper_when_enabled(monkeypatch):
    calls = []

    def fake_cleanup(connection, surrogate_code, srid, grid_name, schema="public"):
        calls.append((connection, surrogate_code, srid, grid_name, schema))
        return {"dropped": ["wp_cty_100_900921"], "missing": []}

    monkeypatch.setattr(
        compute_surrogate.surrogate_utils,
        "cleanup_intermediate_tables",
        fake_cleanup,
    )

    con = object()
    result = compute_surrogate.cleanup_job_intermediate_tables(
        con,
        make_job(),
        make_output_config(cleanup_enabled=True),
        schema="custom",
    )

    assert calls == [(con, 100, 900921, "us12k_516x444", "custom")]
    assert result == {"dropped": ["wp_cty_100_900921"], "missing": []}
