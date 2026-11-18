"""Saved comparisons across uploaded exports and connected platforms."""

from datetime import datetime, timezone
from zipfile import BadZipFile
from hashlib import sha256
from io import BytesIO
import json

import pandas as pd
import streamlit as st

from ..llm import make_client
from ..llm.tasks import write_report
from ..reconciliation import reconcile, report_zip
from .common import store
from .design import header


def _inputs(database, preset, scope):
    tables, details = {}, {}
    platforms = {row["name"] for row in database.sources()}
    snapshots = {f"{row.source} › {row.entity}": row for row in database.latest_snapshots().itertuples()
                 if row.source in platforms}
    for name in st.multiselect("Synced tables", list(snapshots), default=[name for name in preset.get("keys", {}) if name in snapshots],
                               key=f"{scope}:sources"):
        row = snapshots[name]
        frame = database.rows(row.source, row.entity).drop(columns=["_key"], errors="ignore")
        if frame.empty and not len(frame.columns):
            meta = database.snapshot_meta(row.source, row.entity)
            frame = pd.DataFrame(columns=meta.get("fetched_columns", []))
        tables[name] = frame
        details[name] = {"snapshot": row.id, "fetched_at": row.fetched_at, "rows": len(frame)}
    uploads = st.file_uploader("Add local exports", type=["csv", "tsv", "xlsx"], accept_multiple_files=True, key=f"{scope}:uploads")
    used = set()
    for upload in uploads:
        if upload.name in used:
            st.error(f"Rename the duplicate file {upload.name} before uploading both copies.")
            return {}, {}
        used.add(upload.name)
        try:
            data = upload.getvalue()
            if upload.name.lower().endswith(".xlsx"):
                with pd.ExcelFile(BytesIO(data)) as workbook:
                    sheet = st.selectbox(f"Sheet in {upload.name}", workbook.sheet_names, key=f"{scope}:sheet:{upload.name}")
                    frame = workbook.parse(sheet, dtype=str, keep_default_na=False)
                name = f"File › {upload.name} › {sheet}"
            else:
                separator = "\t" if upload.name.lower().endswith(".tsv") else ","
                frame = pd.read_csv(BytesIO(data), sep=separator, dtype=str, keep_default_na=False)
                name = f"File › {upload.name}"
            tables[name] = frame
            details[name] = {"file_sha256": sha256(data).hexdigest(), "rows": len(frame)}
        except (BadZipFile, ValueError, OSError, ImportError) as error:
            st.error(f"Cannot read {upload.name}. {error}")
            return {}, {}
    return tables, details


def _configuration(tables, preset, scope):
    keys, fields = {}, {}
    with st.expander("Match keys and map fields", expanded=True):
        field_text = st.text_input("Fields to compare, separated by commas",
                                   value=", ".join(preset.get("fields", {})), key=f"{scope}:fields",
                                   help="Use common names such as Amount, Status or Location. Leave empty for coverage only.")
        names = list(dict.fromkeys(name.strip() for name in field_text.split(",") if name.strip()))
        fields = {field: {} for field in names}
        for name, frame in tables.items():
            st.markdown(f"**{name}**")
            options = list(frame)
            key = preset.get("keys", {}).get(name)
            if key and key not in options:
                st.warning(f"Saved key column {key} is missing.")
            keys[name] = st.selectbox("Record key", options, index=options.index(key) if key in options else None,
                                     key=f"{scope}:key:{name}", placeholder="Choose the identifying column")
            for field in fields:
                saved = preset.get("fields", {}).get(field, {}).get(name)
                target = saved or next((col for col in options if col.casefold() == field.casefold()), None)
                candidates = [column for column in options if column != keys[name]]
                if saved and saved not in candidates:
                    st.warning(f"Saved mapping for {field} is unavailable.")
                fields[field][name] = st.selectbox(field, candidates,
                                                   index=candidates.index(target) if target in candidates else None,
                                                   key=f"{scope}:map:{name}:{field}", placeholder="Optional")
    return keys, fields


def _search(frame, widget):
    query = st.text_input("Search this table", key=widget).strip()
    if query:
        mask = pd.Series(False, index=frame.index)
        for series in frame.items():
            mask |= series[1].astype("string").str.contains(query, case=False, regex=False, na=False)
        frame = frame.loc[mask]
    st.caption(f"{len(frame):,} rows")
    st.dataframe(frame, hide_index=True, width="stretch")


def _show_report(database, run, scope):
    metadata, coverage, findings = run
    counts = metadata["coverage"]
    row = st.columns(4)
    for column, label in zip(row, ("Values agree", "Missing from expected sources", "Value differences", "Needs review")):
        column.metric(label, counts.get(label, 0))
    st.caption(f"Run {metadata['run_at']} · {len(coverage):,} distinct keys · {len(findings):,} evidence rows")
    records_tab, findings_tab, ai_tab = st.tabs(["Record coverage", "Findings", "AI summary"])
    with records_tab:
        status = st.multiselect("Coverage status", sorted(counts), placeholder="All statuses")
        _search(coverage.loc[coverage.Status.isin(status)] if status else coverage, "recon_coverage")
    with findings_tab:
        choices = st.multiselect("Finding type", sorted(findings.Issue.unique()), placeholder="All finding types")
        _search(findings.loc[findings.Issue.isin(choices)] if choices else findings, "recon_findings")
    with ai_tab:
        client = make_client(database, st.session_state.get("llm_key"))
        st.caption("Set the approved endpoint and model under Review queue → AI model settings. "
                   "This summary sends only aggregate counts, without source names or record values.")
        if st.button("Generate AI summary", key=f"{scope}:ai"):
            try:
                with st.spinner("Writing summary…"):
                    st.session_state[f"{scope}:narrative"] = write_report(client, {
                        "sources": len(metadata["sources"]),
                        "coverage_counts": counts,
                        "finding_counts": metadata["findings"],
                    })
            except Exception as error:
                st.error(f"AI summary unavailable. {error}")
        narrative = st.session_state.get(f"{scope}:narrative")
        if narrative:
            st.markdown(narrative)
    st.download_button("Download full report", report_zip(metadata, coverage, findings, narrative),
                       "reconciliation-report.zip", "application/zip")


def reconciliation_page():
    database = store()
    header("Multi-source comparison", "Match records across exports and connected platforms, then download the evidence.")
    projects = json.loads(database.get_setting("reconciliation_projects", "{}"))
    project = st.selectbox("Saved comparison", [None, *sorted(projects)],
                           format_func=lambda name: name or "New comparison")
    preset = projects.get(project, {})
    scope = f"recon:{project!r}"
    _project(database, projects, project, preset, scope)


def _project(database, projects, project, preset, scope):
    st.caption("Uploads stay in this session. Upload files with the same names next time to reuse their saved mappings.")
    tables, details = _inputs(database, preset, scope)
    missing_inputs = set(preset.get("keys", {})) - set(tables)
    if missing_inputs:
        st.warning("Saved inputs still needed: " + ", ".join(sorted(missing_inputs)))
    if len(tables) >= 2:
        keys, fields = _configuration(tables, preset, scope)
        expected = st.multiselect("Expected sources", list(tables),
                                  default=[source for source in preset.get("expected", tables) if source in tables],
                                  key=f"{scope}:expected", help="Missing records are flagged for these sources.")
        unused = [field for field, mapping in fields.items() if sum(bool(value) for value in mapping.values()) < 2]
        if unused:
            st.warning("Map these fields in at least two tables to compare them: " + ", ".join(unused))
        valid = all(keys.values()) and bool(expected) and not unused and not missing_inputs
        name = st.text_input("Comparison name", value=project or "", key=f"{scope}:name")
        save, run = st.columns(2)
        if save.button("Save setup", disabled=not valid or not name.strip()):
            projects[name.strip()] = {"keys": keys, "fields": fields, "expected": expected}
            database.set_setting("reconciliation_projects", json.dumps(projects))
            st.success(f"Saved {name.strip()}.")
        if run.button("Run comparison", disabled=not valid, type="primary"):
            coverage, findings = reconcile(tables, keys, fields, expected)
            metadata = {
                "project": name.strip(),
                "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sources": details,
                "expected": expected,
                "keys": keys,
                "fields": fields,
                "coverage": coverage.Status.value_counts().to_dict(),
                "findings": findings.Issue.value_counts().to_dict(),
            }
            history = json.loads(database.get_setting("reconciliation_history", "[]"))
            database.set_setting("reconciliation_history", json.dumps([metadata, *history[:49]], default=str))
            st.session_state[f"{scope}:run"] = (metadata, coverage, findings)
            st.session_state.pop(f"{scope}:narrative", None)
    else:
        st.info("Select or upload at least two tables to begin.")
    if result := st.session_state.get(f"{scope}:run"):
        st.subheader("Latest result in this session")
        _show_report(database, result, scope)
    history = json.loads(database.get_setting("reconciliation_history", "[]"))
    if history:
        with st.expander("Recent runs"):
            st.dataframe(pd.DataFrame([{
                "Run time": row["run_at"], "Comparison": row["project"],
                "Sources": len(row["sources"]), "Keys": sum(row["coverage"].values()),
                "Finding rows": sum(row["findings"].values()),
            } for row in history]), hide_index=True)
