import json

import pandas as pd
import pytest

from dqp.clean import CleanConfig, apply
from dqp.engine import sync_source, validate
from dqp.store import Store

ROWS = [
    {"_key": "1", "Name": "  Ann   Lee ", "CDL": "D123456", "Status": "Active", "Notes": "call before 9", "SSN": "111-22-3333"},
    {"_key": "2", "Name": "Bob Ray", "CDL": "N/A", "Status": "Terminated", "Notes": None, "SSN": "444-55-6666"},
    {"_key": "3", "Name": "Bob Ray", "CDL": "N/A", "Status": "Terminated", "Notes": None, "SSN": "444-55-6666"},
    {"_key": "4", "Name": None, "CDL": "-", "Status": "", "Notes": "x", "SSN": None},
]
MAPPED = ["Name", "CDL", "Status"]


def test_unmapped_columns_are_dropped_and_values_tidied():
    out, meta = apply(ROWS, CleanConfig(), MAPPED)
    assert set(out[0]) == {"_key", "Name", "CDL", "Status"}
    assert out[0]["Name"] == "Ann Lee" and out[1]["CDL"] is None
    assert len(out) == 3 and meta["rows_empty"] == 1             # row 4 has nothing in the kept columns
    assert set(meta["dropped_columns"]) == {"Notes", "SSN"}


def test_dropped_columns_keep_names_and_counts_but_never_values():
    _, meta = apply(ROWS, CleanConfig(), MAPPED)
    assert meta["dropped_columns"] == {"Notes": 2, "SSN": 3}
    assert "111-22-3333" not in json.dumps(meta) and "call before 9" not in json.dumps(meta)


def test_extra_keep_and_keep_all():
    out, _ = apply(ROWS, CleanConfig(extra_keep=["Notes"]), MAPPED)
    assert "Notes" in out[0] and "SSN" not in out[0]
    out, meta = apply(ROWS, CleanConfig(keep_all=True), MAPPED)
    assert "SSN" in out[0] and not meta["dropped_columns"]


def test_filters_can_use_columns_that_are_not_kept():
    cfg = CleanConfig(filters=[{"column": "Status", "op": "is not in", "values": ["terminated"]}])
    out, meta = apply(ROWS, cfg, MAPPED)
    assert [r["_key"] for r in out] == ["1"] and meta["rows_filtered"] == 2   # rows 2 and 3 filtered; row 4 has a blank status so it passes, then is empty
    cfg = CleanConfig(filters=[{"column": "SSN", "op": "has a value", "values": []}])
    assert [r["_key"] for r in apply(ROWS, cfg, MAPPED)[0]] == ["1", "2", "3"]


def test_unfinished_or_unknown_filters_never_delete_everything():
    out, meta = apply(ROWS, CleanConfig(filters=[{"column": "Status", "op": "is in", "values": []},
                                                  {"column": "Nope", "op": "is in", "values": ["x"]}]), MAPPED)
    assert len(out) == 3 and meta["unknown_filter_columns"] == ["Nope"]


def test_duplicate_rows_only_dropped_when_asked():
    assert len(apply(ROWS, CleanConfig(), MAPPED)[0]) == 3
    out, meta = apply(ROWS, CleanConfig(drop_duplicate_rows=True), MAPPED)
    assert len(out) == 2 and meta["rows_duplicate"] == 1


def test_switching_tidy_options_off_keeps_raw_text():
    out, _ = apply(ROWS, CleanConfig(trim=False, blanks_to_null=False), MAPPED)
    assert out[0]["Name"] == "  Ann   Lee " and out[1]["CDL"] == "N/A"


# ---- through the real sync ----------------------------------------------------------------------
@pytest.fixture()
def st_with_file(tmp_path):
    path = tmp_path / "drivers.xlsx"
    pd.DataFrame({"Driver Name": ["Ann Lee", "Bob Ray"], "CDL #": ["D1234567", "K7654321"], "Status": ["Active", "Terminated"],
                  "Home Address": ["1 Main St", "2 Oak Ave"], "Notes": ["a", "b"]}).to_excel(path, sheet_name="Drivers", index=False)
    st = Store(str(tmp_path / "t.db"))
    st.add_source("Excel", "excel", {"path": str(path)})
    return st


def test_sync_saves_only_needed_columns_and_reports_it(st_with_file):
    st = st_with_file
    assert sync_source(st, "Excel") == {"Drivers": 2}
    df = st.rows("Excel", "Drivers")
    assert set(df.columns) == {"_key", "Driver Name", "CDL #", "Status"}          # address and notes never stored
    meta = st.snapshot_meta("Excel", "Drivers")
    assert meta["fetched_rows"] == 2 and set(meta["dropped_columns"]) == {"Home Address", "Notes"}
    assert "1 Main St" not in json.dumps(meta)


def test_changing_rules_and_refetching_brings_a_column_back(st_with_file):
    st = st_with_file
    sync_source(st, "Excel")
    st.save_clean("Excel", "Drivers", CleanConfig(extra_keep=["Notes"]).dump())
    sync_source(st, "Excel")
    assert "Notes" in st.rows("Excel", "Drivers").columns and "Home Address" not in st.rows("Excel", "Drivers").columns


def test_filtered_rows_show_up_as_missing_from_that_platform(tmp_path, st_with_file):
    st = st_with_file
    other = tmp_path / "eld.csv"
    pd.DataFrame({"Driver": ["Ann Lee", "Bob Ray"], "License": ["D1234567", "K7654321"]}).to_csv(other, index=False)
    st.add_source("ELD", "excel", {"path": str(other)})
    sync_source(st, "Excel")
    sync_source(st, "ELD")
    validate(st)
    assert not (st.issues().rule == "missing_in_platform").any()
    st.save_clean("Excel", "Drivers", CleanConfig(filters=[{"column": "Status", "op": "is not in", "values": ["terminated"]}]).dump())
    sync_source(st, "Excel")
    validate(st)
    miss = st.issues()
    miss = miss[(miss.rule == "missing_in_platform") & (miss.status == "open")]
    assert len(miss) == 1 and "Bob Ray" in miss.iloc[0].message and miss.iloc[0].source == "Excel"


def test_raw_copy_is_optional_and_purged(st_with_file):
    st = st_with_file
    sync_source(st, "Excel")
    assert st.archived_rows("Excel", "Drivers").empty                           # off by default
    st.save_clean("Excel", "Drivers", CleanConfig(keep_raw=True).dump())
    sync_source(st, "Excel")
    assert "Home Address" in st.archived_rows("Excel", "Drivers").columns        # untouched copy has everything
    with st.conn() as c:
        c.execute("UPDATE snapshots SET fetched_at='2020-01-01T00:00:00+00:00'")
    assert st.purge_raw_archive(7) == 2 and st.archived_rows("Excel", "Drivers").empty


def test_old_database_gets_the_new_column(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, entity TEXT, fetched_at TEXT, row_count INTEGER)")
    c.commit(); c.close()
    st = Store(str(p))
    assert st.save_snapshot("a", "b", [{"_key": "1"}]) == 1
