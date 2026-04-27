import sys

import utils


class FakeConnection:
    def __init__(self, tables):
        self.tables = list(tables)
        self.drop_calls = []
        self.disconnect_called = False

    def list_tables(self, database=None):
        self.list_tables_database = database
        return list(self.tables)

    def drop_table(self, name, /, *, database=None, force=False):
        self.drop_calls.append((name, database, force))
        if name in self.tables:
            self.tables.remove(name)

    def disconnect(self):
        self.disconnect_called = True


def test_get_intermediate_table_names_returns_stage_order():
    assert utils.get_intermediate_table_names(100, 900921, "us12k_516x444") == [
        "wp_cty_100_900921",
        "wp_cty_cell_100_us12k_516x444",
        "numer_100_us12k_516x444",
        "denom_100_us12k_516x444",
        "surg_100_us12k_516x444",
    ]


def test_cleanup_intermediate_tables_drops_only_existing_tables_in_stage_order():
    con = FakeConnection(
        [
            "surg_100_us12k_516x444",
            "wp_cty_100_900921",
            "numer_100_us12k_516x444",
        ]
    )

    result = utils.cleanup_intermediate_tables(
        con,
        surrogate_code=100,
        srid=900921,
        grid_name="us12k_516x444",
        schema="public",
    )

    assert con.drop_calls == [
        ("wp_cty_100_900921", "public", True),
        ("numer_100_us12k_516x444", "public", True),
        ("surg_100_us12k_516x444", "public", True),
    ]
    assert result == {
        "dropped": [
            "wp_cty_100_900921",
            "numer_100_us12k_516x444",
            "surg_100_us12k_516x444",
        ],
        "missing": [
            "wp_cty_cell_100_us12k_516x444",
            "denom_100_us12k_516x444",
        ],
    }


def test_cleanup_intermediate_subcommand_calls_helper(monkeypatch, capsys):
    con = FakeConnection([])
    calls = {}

    monkeypatch.setattr(utils.db_utils, "load_config", lambda path: {"path": path})
    monkeypatch.setattr(utils.db_utils, "connect_db", lambda config: con)

    def fake_cleanup(connection, surrogate_code, srid, grid_name, schema="public"):
        calls["connection"] = connection
        calls["surrogate_code"] = surrogate_code
        calls["srid"] = srid
        calls["grid_name"] = grid_name
        calls["schema"] = schema
        return {"dropped": ["wp_cty_100_900921"], "missing": []}

    monkeypatch.setattr(utils, "cleanup_intermediate_tables", fake_cleanup)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "utils.py",
            "cleanup-intermediate",
            "--code",
            "100",
            "--srid",
            "900921",
            "--grid",
            "us12k_516x444",
            "--schema",
            "custom",
            "--config",
            "database_config.csv",
        ],
    )

    utils.main()
    stdout = capsys.readouterr().out

    assert calls == {
        "connection": con,
        "surrogate_code": 100,
        "srid": 900921,
        "grid_name": "us12k_516x444",
        "schema": "custom",
    }
    assert "Dropped 1 intermediate table(s)" in stdout
    assert con.disconnect_called is True
