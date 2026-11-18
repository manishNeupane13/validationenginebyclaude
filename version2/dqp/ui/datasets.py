"""Compare and validate arbitrary uploaded tables."""

from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import streamlit as st

from ..tabular import clean_values, compare, grouped_summary, profile, record_views, valid_values
from .common import store
from .design import header


def _upload(side):
    database = store()
    snapshots = list(database.latest_snapshots().itertuples())
    if snapshots:
        selection = st.selectbox(
            f"{side} source", [None, *range(len(snapshots))],
            format_func=lambda i: "Upload a file" if i is None else
            f"{snapshots[i].source} › {snapshots[i].entity}",
            key=f"dataset_source_{side}",
        )
        if selection is not None:
            snapshot = snapshots[selection]
            frame = database.rows(snapshot.source, snapshot.entity)
            return frame.drop(columns=["_key"], errors="ignore")
    upload = st.file_uploader(f"{side} dataset", type=["csv", "tsv", "xlsx"], key=f"table_{side}")
    if upload is None:
        return None
    try:
        upload.seek(0)
        if Path(upload.name).suffix.lower() == ".xlsx":
            with pd.ExcelFile(upload) as workbook:
                sheet = st.selectbox(f"{side} sheet", workbook.sheet_names, key=f"sheet_{side}")
                return workbook.parse(sheet, dtype=str, keep_default_na=False)
        separator = "\t" if upload.name.lower().endswith(".tsv") else ","
        return pd.read_csv(upload, sep=separator, dtype=str, keep_default_na=False)
    except (ValueError, OSError, ImportError) as error:
        st.error(f"Could not read {upload.name}. {error}")
        return None


def _browse(frame, key, label="Search records"):
    query = st.text_input(label, key=f"search_{key}", placeholder="Enter any value from the table").strip()
    if query:
        matches = pd.Series(False, index=frame.index)
        for column in frame:
            matches |= frame[column].astype("string").str.contains(query, case=False, regex=False, na=False)
        frame = frame.loc[matches]
    st.caption(f"{len(frame):,} records")
    if frame.empty:
        st.info("No records in this view.")
        return
    st.dataframe(frame, hide_index=True, width="stretch")
    st.download_button("Download these rows", frame.to_csv(index=False), f"{key}.csv", "text/csv",
                       key=f"download_{key}")


def _group(frame, side):
    columns = st.multiselect("Group by", list(frame), key=f"group_{side}",
                             placeholder="Choose columns such as location or status")
    if not columns:
        return
    measure = st.selectbox("Numeric field for totals and averages", list(frame), index=None,
                           key=f"measure_{side}", placeholder="Optional")
    _browse(grouped_summary(frame, columns, measure), f"groups_{side}", "Search groups")


def _profile(frame, side):
    summary = profile(frame)
    records, groups, overview, explore, validate = st.tabs(["Browse", "Group", "Profile", "Explore", "Validate"])
    with records:
        _browse(frame, side)
    with groups:
        _group(frame, side)
    with overview:
        st.dataframe(summary, hide_index=True, width="stretch")
    with explore:
        column = st.selectbox("Explore column", list(frame), key=f"explore_{side}")
        values = clean_values(frame[column]).dropna()
        if values.empty:
            st.info("This column has no filled values.")
        elif valid_values(values, "Number").all():
            numbers = pd.to_numeric(values)
            quantiles = numbers.quantile([.25, .5, .75])
            spread = quantiles.loc[.75] - quantiles.loc[.25]
            outliers = (numbers < quantiles.loc[.25] - 1.5 * spread) | (numbers > quantiles.loc[.75] + 1.5 * spread)
            stats = pd.DataFrame({
                "Statistic": ["Min", "25th percentile", "Median", "75th percentile", "Max", "Mean", "Potential outliers"],
                "Value": [numbers.min(), *quantiles, numbers.max(), numbers.mean(), int(outliers.sum())],
            })
            st.dataframe(stats, hide_index=True, width="stretch")
            st.caption("Potential outliers fall outside 1.5 times the interquartile range.")
            if numbers.nunique() > 1:
                bins = pd.cut(numbers, bins=min(12, numbers.nunique()), duplicates="drop").value_counts(sort=False)
                bins.index = bins.index.astype(str)
                st.bar_chart(bins.rename("Rows"), sort=False)
        else:
            st.caption("Most frequent values")
            st.dataframe(values.value_counts().head(15).rename_axis("Value").reset_index(name="Rows"),
                         hide_index=True, width="stretch")
    with validate:
        column = st.selectbox("Validate column", list(frame), key=f"validate_{side}")
        kind = st.selectbox("Expected type", ["Text", "Number", "Date", "Boolean"], key=f"type_{side}")
        left, right = st.columns(2)
        required = left.checkbox("Required", key=f"required_{side}")
        unique = right.checkbox("Unique", key=f"unique_{side}")
        allowed_input = st.text_area("Allowed values", placeholder="One value per line", key=f"allowed_{side}")
        allowed = {value.strip() for value in allowed_input.splitlines() if value.strip()}
        values = clean_values(frame[column])
        flags = pd.DataFrame({
            "Row": range(2, len(frame) + 2),
            "Value": values,
            "Missing required value": values.isna() & required,
            "Invalid type": values.notna() & ~valid_values(values, kind),
            "Duplicate value": values.notna() & values.duplicated(keep=False) & unique,
            "Outside allowed values": values.notna() & ~values.isin(allowed) & bool(allowed),
        })
        if kind == "Number":
            a, b = st.columns(2)
            lower = a.number_input("Minimum", value=None, key=f"min_{side}")
            upper = b.number_input("Maximum", value=None, key=f"max_{side}")
            if lower is not None and upper is not None and lower > upper:
                st.error("Minimum must be less than or equal to maximum.")
                return
            numbers = pd.to_numeric(values, errors="coerce")
            if lower is not None:
                flags["Below minimum"] = numbers.lt(lower).fillna(False)
            if upper is not None:
                flags["Above maximum"] = numbers.gt(upper).fillna(False)
        problems = flags.loc[flags.iloc[:, 2:].any(axis=1)]
        st.caption(f"{len(problems):,} rows with issues")
        if not problems.empty:
            st.dataframe(problems, hide_index=True, width="stretch")
            st.download_button("Download validation findings", problems.to_csv(index=False),
                               f"{side.lower()}-validation.csv", "text/csv", key=f"validate_export_{side}")


def _dataset_panel(side):
    with st.container(border=True):
        st.subheader(f"{side} dataset")
        frame = _upload(side)
        if frame is not None and len(frame.columns):
            stats = st.columns(3)
            stats[0].metric("Rows", f"{len(frame):,}")
            stats[1].metric("Columns", f"{len(frame.columns):,}")
            stats[2].metric("Duplicate rows", f"{frame.duplicated(keep=False).sum():,}")
            _profile(frame, side)
    return frame


def _header(value):
    return "".join(c for c in str(value).casefold() if c.isalnum())


def datasets_page():
    header("Compare datasets", "Browse and profile one dataset, or add a second to see where records match and where they differ.")
    left_panel, right_panel = st.columns(2)
    with left_panel:
        left = _dataset_panel("Left")
    with right_panel:
        right = _dataset_panel("Right")
    if left is None or right is None or not len(left.columns) or not len(right.columns):
        return

    st.subheader("Match records")
    st.caption("Choose a unique identifier on each side. Key and value comparisons trim surrounding whitespace and preserve case and leading zeros.")
    left_key = st.selectbox("Left key column", list(left))
    right_columns = list(right)
    matching = [column for column in right if _header(column) == _header(left_key)]
    right_key = st.selectbox("Right key column", right_columns,
                             index=right_columns.index(matching[0]) if matching else 0)

    views = record_views(left, right, left_key, right_key)
    totals = st.columns(3)
    for metric, name in zip(totals, ("Matched", "Left only", "Right only")):
        metric.metric(name, f"{len(views[name]):,}")

    records, differences = st.tabs(["Record coverage", "Value differences"])
    with records:
        selected = st.radio("Show records", list(views), horizontal=True)
        if selected.endswith("key issues"):
            st.caption("Includes rows with blank keys or keys that appear more than once in either dataset.")
        _browse(views[selected], "coverage")
    with differences:
        _differences(left, right, left_key, right_key)


def _differences(left, right, left_key, right_key):
    st.subheader("Map columns")
    candidates = [column for column in right if column != right_key]
    suggestions = []
    suggested_targets = set()
    for column in left:
        if column == left_key:
            continue
        scores = sorted(((SequenceMatcher(None, _header(column), _header(other)).ratio(), other)
                         for other in candidates if other not in suggested_targets), reverse=True)
        target = scores[0][1] if scores and scores[0][0] >= .85 else None
        if target is not None:
            suggested_targets.add(target)
        suggestions.append({"Left column": column, "Right column": target})
    mapping = {}
    if suggestions and candidates:
        edited = st.data_editor(pd.DataFrame(suggestions), hide_index=True, disabled=["Left column"],
                                column_config={"Right column": st.column_config.SelectboxColumn(options=candidates)})
        mapping = {row["Left column"]: row["Right column"] for row in edited.to_dict("records")
                   if pd.notna(row["Right column"])}
        if len(set(mapping.values())) != len(mapping):
            st.error("Select a different right column for each mapped left column.")
            return
    if not mapping:
        st.info("Map at least one column to compare values.")
        return
    results = compare(left, right, left_key, right_key, mapping)
    results = results.loc[results["Issue"].isin(["Mismatch", "Missing value"])]
    if results.empty:
        st.success("No value differences across the matched records and mapped columns.")
        return
    _browse(results, "differences", "Search differences")
