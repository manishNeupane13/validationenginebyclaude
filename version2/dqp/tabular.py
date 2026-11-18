"""Schema-independent profiling and keyed comparison for tabular datasets."""

import math

import pandas as pd


def clean_values(values: pd.Series) -> pd.Series:
    values = values.astype("string").str.strip()
    return values.mask(values.eq(""))


def valid_values(values: pd.Series, kind: str) -> pd.Series:
    if kind == "Number":
        numbers = pd.to_numeric(values, errors="coerce")
        return numbers.notna() & ~numbers.isin([math.inf, -math.inf])
    if kind == "Date":
        return pd.to_datetime(values, errors="coerce", format="mixed").notna()
    if kind == "Boolean":
        return values.str.casefold().isin(["true", "false", "yes", "no"])
    return values.notna()


def profile(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for column in frame:
        values = clean_values(frame[column]).dropna()
        kind = "Empty" if values.empty else "Text"
        if not values.empty:
            for candidate in ("Boolean", "Number", "Date"):
                if valid_values(values, candidate).all():
                    kind = candidate
                    break
        rows.append({
            "Column": column,
            "Inferred type": kind,
            "Missing": len(frame) - len(values),
            "Missing %": round(100 * (len(frame) - len(values)) / len(frame), 1) if len(frame) else 0.0,
            "Distinct": values.nunique(),
            "Rows with repeated values": int(values.duplicated(keep=False).sum()),
        })
    return pd.DataFrame(rows)


def compare(left, right, left_key, right_key, mapping):
    """Compare one-to-one keys and report ambiguous keys without joining them."""
    columns = ["Issue", "Key", "Left column", "Right column", "Left value", "Right value", "Left row", "Right row"]
    issues = []

    def index(frame, key, side):
        frame = frame.reset_index(drop=True)
        keys = clean_values(frame[key])
        repeated = keys.notna() & keys.duplicated(keep=False)
        for row in frame.index[keys.isna()]:
            issues.append([f"Missing {side} key", None, key if side == "left" else None,
                           key if side == "right" else None, None, None, row + 2 if side == "left" else None,
                           row + 2 if side == "right" else None])
        for value in keys[repeated].unique():
            issues.append([f"Duplicate {side} key", value, None, None, None, None])
        present = keys.notna()
        return frame.loc[present].set_index(keys[present]), set(keys[repeated])

    left, left_duplicates = index(left, left_key, "left")
    right, right_duplicates = index(right, right_key, "right")
    ambiguous = left_duplicates | right_duplicates
    left = left.loc[~left.index.isin(ambiguous)]
    right = right.loc[~right.index.isin(ambiguous)]
    for key in left.index.difference(right.index, sort=False):
        issues.append(["Only in left", key, None, None, None, None])
    for key in right.index.difference(left.index, sort=False):
        issues.append(["Only in right", key, None, None, None, None])
    shared = left.index.intersection(right.index, sort=False)
    for left_column, right_column in mapping.items():
        a = clean_values(left.loc[shared, left_column])
        b = clean_values(right.loc[shared, right_column])
        changed = a.isna().ne(b.isna()) | a.ne(b).fillna(False)
        for key in shared[changed]:
            status = "Missing value" if pd.isna(a[key]) or pd.isna(b[key]) else "Mismatch"
            issues.append([status, key, left_column, right_column, a[key], b[key]])
    return pd.DataFrame([row + [None] * (len(columns) - len(row)) for row in issues], columns=columns)


def record_views(left, right, left_key, right_key):
    """Group records by coverage, retaining the original values on both sides."""
    a, b = clean_values(left[left_key]), clean_values(right[right_key])
    ambiguous = set(a[a.notna() & a.duplicated(keep=False)]) | set(b[b.notna() & b.duplicated(keep=False)])
    left_valid = a.notna() & ~a.isin(ambiguous)
    right_valid = b.notna() & ~b.isin(ambiguous)
    left_matched = left_valid & a.isin(b[right_valid])
    right_matched = right_valid & b.isin(a[left_valid])
    left_rows = left.loc[left_matched].set_index(a[left_matched]).add_prefix("Left · ")
    right_rows = right.loc[right_matched].set_index(b[right_matched]).add_prefix("Right · ")
    matched = left_rows.join(right_rows).rename_axis("Match key").reset_index()
    return {
        "Matched": matched,
        "Left only": left.loc[left_valid & ~left_matched],
        "Right only": right.loc[right_valid & ~right_matched],
        "Left key issues": left.loc[~left_valid],
        "Right key issues": right.loc[~right_valid],
    }


def grouped_summary(frame, columns, measure=None):
    """Count records and summarize finite numbers within each selected group."""
    groups = [clean_values(frame[column]).rename(column) for column in columns]
    if measure is None:
        return frame.groupby(groups, dropna=False, sort=False).size().to_frame("Rows").reset_index(
            names=[f"Group · {column}" for column in columns]
        ).sort_values("Rows", ascending=False)
    values = clean_values(frame[measure])
    numbers = pd.to_numeric(values.where(valid_values(values, "Number")), errors="coerce")
    totals = numbers.groupby(groups, dropna=False, sort=False).agg(
        Rows="size", Numeric_values="count", Total=lambda group: group.sum(min_count=1), Average="mean",
    )
    return totals.reset_index(names=[f"Group · {column}" for column in columns]).sort_values("Rows", ascending=False)
