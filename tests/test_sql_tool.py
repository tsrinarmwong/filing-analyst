"""Tests for the read-only SQL guard. The pure validator tests run anywhere; the
execution tests run against the built DB and skip if it hasn't been built yet."""
import pytest

import config
from app import sql_tool

# --- validator: must REJECT -------------------------------------------------
REJECTED = [
    "",                                             # empty
    "   ",                                           # whitespace only
    "INSERT INTO metrics VALUES (1)",               # write
    "UPDATE metrics SET value=0",                   # write
    "DELETE FROM metrics",                          # write
    "DROP TABLE metrics",                           # DDL
    "ALTER TABLE metrics ADD COLUMN x",             # DDL
    "CREATE TABLE t (x INT)",                        # DDL
    "ATTACH DATABASE 'x.db' AS y",                  # reach outside
    "PRAGMA table_info(metrics)",                   # pragma
    "VACUUM",                                         # maintenance write
    "SELECT 1; DROP TABLE metrics",                 # multiple statements
    "SELECT 1; SELECT 2",                           # multiple statements
    "WITH x AS (SELECT 1) DELETE FROM metrics",     # write hidden behind CTE
    "EXPLAIN SELECT * FROM metrics",                # not a plain SELECT/WITH
]


@pytest.mark.parametrize("sql", REJECTED)
def test_rejects_non_readonly(sql):
    # Every rejected query must produce an error string from validate().
    assert sql_tool.validate(sql) is not None


# --- validator: must ACCEPT -------------------------------------------------
ACCEPTED = [
    "SELECT 1",
    "SELECT value FROM metrics WHERE metric='revenue'",
    "select * from companies;",                      # single trailing semicolon ok
    "WITH r AS (SELECT value FROM metrics) SELECT * FROM r",   # read-only CTE
    "SELECT REPLACE(name, 'Inc', '') FROM companies",          # REPLACE() string fn allowed
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_accepts_readonly_selects(sql):
    assert sql_tool.validate(sql) is None


# --- execution against the real DB ------------------------------------------
db_required = pytest.mark.skipif(
    not config.DB_PATH.exists(),
    reason="financials.db not built; run `python -m scripts.build_financials_db`",
)


@db_required
def test_run_sql_returns_rows_and_echoes_sql():
    sql = "SELECT ticker, value FROM metrics WHERE metric='revenue' AND ticker='NVDA'"
    out = sql_tool.run_sql(sql)
    assert "error" not in out
    assert out["sql"] == sql           # SQL echoed back for the evidence trail
    assert out["row_count"] >= 1
    assert all(r["ticker"] == "NVDA" for r in out["rows"])


@db_required
def test_run_sql_blocks_writes_without_touching_db():
    out = sql_tool.run_sql("DELETE FROM metrics")
    assert "error" in out and "rows" not in out
    # The data is still intact.
    check = sql_tool.run_sql("SELECT COUNT(*) AS n FROM metrics")
    assert check["rows"][0]["n"] > 0


@db_required
def test_row_cap_enforced(monkeypatch):
    monkeypatch.setattr(sql_tool, "MAX_ROWS", 3)
    out = sql_tool.run_sql("SELECT * FROM metrics")
    assert out["row_count"] == 3
    assert "note" in out  # truncation is reported


@db_required
def test_readonly_connection_rejects_write_even_if_validator_bypassed():
    # Prove the second layer: go around validate() and hit the ro connection.
    con = sql_tool._connect()
    with pytest.raises(Exception):
        con.execute("DELETE FROM metrics")
    con.close()
