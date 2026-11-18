"""Exact multi-source reconciliation and portable evidence reports."""
from collections import defaultdict
from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from .tabular import clean_values

FINDING_COLUMNS = ["Issue", "Key", "Source", "Field", "Value", "Row"]


def reconcile(tables, keys, fields, expected=None):
    """Compare only unique keys, retaining source coverage and conflicting values."""
    expected = list(tables) if expected is None else expected
    presence = defaultdict(dict)
    findings = []
    normalized = {}
    for name, frame in tables.items():
        ids = clean_values(frame[keys[name]])
        normalized[name] = {field: clean_values(frame[column]) for field, mapping in fields.items()
                            if (column := mapping.get(name))}
        for position, key in enumerate(ids):
            if pd.isna(key):
                findings.append(("Blank key", None, name, keys[name], None, position + 2))
            else:
                presence[key].setdefault(name, []).append(position)

    coverage = []
    for key, sources in presence.items():
        ambiguous = any(len(positions) > 1 for positions in sources.values())
        missing = [name for name in expected if name not in sources]
        different = False
        checked = False
        for name in missing:
            findings.append(("Missing record", key, name, None, None, None))
        if ambiguous:
            for name, positions in sources.items():
                if len(positions) > 1:
                    for position in positions:
                        findings.append(("Duplicate key", key, name, keys[name], key, position + 2))
        else:
            for field in fields:
                values = {name: normalized[name][field].iloc[positions[0]]
                          for name, positions in sources.items() if field in normalized[name]}
                if len(values) < 2:
                    continue
                checked = True
                filled = {value for value in values.values() if pd.notna(value)}
                conflict = len(filled) > 1
                gap = bool(filled) and any(pd.isna(value) for value in values.values())
                if conflict or gap:
                    different = True
                    for name, value in values.items():
                        issue = "Missing value" if pd.isna(value) else "Conflicting value" if conflict else "Value present"
                        findings.append((issue, key, name, field, value, sources[name][0] + 2))
        status = ("Needs review" if ambiguous else "Missing from expected sources" if missing else
                  "Value differences" if different else "Values agree" if checked else "Present · keys only")
        coverage.append([key, status, len(sources),
                         *[len(sources.get(name, [])) for name in tables]])

    return (pd.DataFrame(coverage, columns=["Key", "Status", "Sources present",
                                           *[f"Rows · {name}" for name in tables]]),
            pd.DataFrame(findings, columns=FINDING_COLUMNS))


def report_zip(metadata, coverage, findings, narrative=None):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as report:
        report.writestr("run.json", json.dumps(metadata, indent=2, default=str))
        report.writestr("record-coverage.csv", coverage.to_csv(index=False))
        report.writestr("findings.csv", findings.to_csv(index=False))
        if narrative:
            report.writestr("ai-summary.md", narrative)
    return output.getvalue()
