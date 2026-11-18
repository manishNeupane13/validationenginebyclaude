from io import BytesIO
from zipfile import ZipFile

import pandas as pd

from dqp.reconciliation import reconcile, report_zip


def test_reconciliation_records_coverage_and_evidence():
    tables = {
        "Excel": pd.DataFrame({"ID": [" 001 ", "002", "003", "004", "004", ""],
                               "Cost": ["10", "20", "30", "40", "40", "50"]}),
        "Samsara": pd.DataFrame({"Ref": ["001", "002", "004"], "Total": ["10", "25", "40"]}),
        "Ledger": pd.DataFrame({"No": ["001", "002", "004"], "Net": ["10", "", "40"]}),
    }
    keys = {"Excel": "ID", "Samsara": "Ref", "Ledger": "No"}
    fields = {"Amount": {"Excel": "Cost", "Samsara": "Total", "Ledger": "Net"}}
    coverage, findings = reconcile(tables, keys, fields, ["Excel", "Samsara"])
    assert coverage.set_index("Key").Status.to_dict() == {
        "001": "Values agree", "002": "Value differences",
        "003": "Missing from expected sources", "004": "Needs review",
    }
    assert findings.Issue.value_counts().to_dict() == {
        "Conflicting value": 2, "Duplicate key": 2, "Blank key": 1, "Missing value": 1, "Missing record": 1,
    }
    assert findings.loc[findings.Issue.eq("Missing record"), "Source"].tolist() == ["Samsara"]
    assert findings.loc[findings.Issue.eq("Blank key"), "Row"].tolist() == [7]
    with ZipFile(BytesIO(report_zip({"project": "Test"}, coverage, findings))) as report:
        assert set(report.namelist()) == {"run.json", "record-coverage.csv", "findings.csv"}
        assert b"001,Values agree,3" in report.read("record-coverage.csv")


def test_keys_only_and_empty_tables():
    tables = {"A": pd.DataFrame({"id": ["01", "02"]}), "B": pd.DataFrame({"id": ["01"]}),
              "C": pd.DataFrame({"id": []})}
    keys = dict.fromkeys(tables, "id")
    coverage, findings = reconcile(tables, keys, {}, ["A", "B"])
    assert coverage.Status.tolist() == ["Present · keys only", "Missing from expected sources"]
    assert findings.Source.tolist() == ["B"]


def test_source_metadata_includes_schema_and_key_quality():
    from dqp.reconciliation import source_metadata

    tables = {"Export": pd.DataFrame({"ID": ["01", " 01 ", ""], "Label": ["A", "A", "B"]})}
    metadata = source_metadata(tables, {"Export": "ID"}, {"Name": {"Export": "Label"}},
                               {"Export": {"input_type": "upload", "filename": "items.csv"}}, ["Export"])
    source = metadata["Export"]
    assert source["filename"] == "items.csv"
    assert source["expected"] is True
    assert (source["blank_key_rows"], source["duplicate_key_rows"], source["duplicate_keys"]) == (1, 2, 1)
    assert source["mapped_fields"] == {"Name": "Label"}
    assert [(column["name"], column["distinct_nonblank"]) for column in source["columns"]] == [
        ("ID", 1), ("Label", 2)
    ]
