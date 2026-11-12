from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from ..engine import sync_and_validate
from ..scheduler import Scheduler
from ..store import Store
from ..workspace import Workspace, load_workspace

OPS = ["is in", "is not in", "contains", "is empty", "has a value", "appears more than once"]


@st.cache_resource
def store() -> Store:
    return Store()


@st.cache_resource
def scheduler() -> Scheduler:
    path = store().path
    return Scheduler(lambda: sync_and_validate(Store(path)))


def version() -> str:
    return store().data_version()


@st.cache_data(show_spinner=False)
def workspace(v: str) -> Workspace:
    return load_workspace(store())


@st.cache_data(show_spinner=False)
def raw_frames(v: str) -> dict[tuple[str, str], pd.DataFrame]:
    s = store()
    return {(r.source, r.entity): s.rows(r.source, r.entity) for r in s.latest_snapshots().itertuples()}


def session_overrides() -> dict[str, dict[str, Any]]:
    return st.session_state.setdefault("tokens", {})


def run_everything() -> dict[str, Any]:
    with st.spinner("Fetching every platform and checking the data…"):
        summary = sync_and_validate(store(), session_overrides())
    st.cache_data.clear()
    return summary


def open_issue_map(source: str | None = None) -> dict[str, set[str]]:
    """record id -> set of rule names, for open issues only."""
    df = store().issues()
    out: dict[str, set[str]] = {}
    for r in df[df.status == "open"].itertuples():
        for rid in json.loads(r.record_ids or "[]"):
            if source is None or rid.startswith(f"{source}:"):
                out.setdefault(rid, set()).add(r.rule)
    return out


def filter_df(df: pd.DataFrame, key: str, hint: str = "Type a name, ID, VIN or phone number") -> pd.DataFrame:
    """Search box + per-column filters (is in / is not in / contains / empty / duplicated)."""
    c1, c2 = st.columns([3, 2])
    q = c1.text_input("Search all columns", key=f"{key}_q", placeholder=hint)
    cols = [c for c in df.columns if not str(c).startswith("_")]
    picked = c2.multiselect("Filter by column", cols, key=f"{key}_fc", placeholder="Choose columns to filter")
    out = df
    if q.strip():
        t = out.astype(str).apply(lambda c: c.str.lower().str.contains(q.strip().lower(), regex=False))
        out = out[t.any(axis=1)]
    for c in picked:
        a, b = st.columns([1, 3])
        op = a.selectbox(f"{c}", OPS, key=f"{key}_op_{c}")
        s = out[c]
        if op in ("is in", "is not in"):
            opts = sorted(df[c].dropna().astype(str).unique().tolist())[:2000]
            vals = b.multiselect("Values", opts, key=f"{key}_v_{c}", label_visibility="collapsed")
            if vals:
                m = s.astype(str).isin(vals)
                out = out[m] if op == "is in" else out[~m]
        elif op == "contains":
            t = b.text_input("Text", key=f"{key}_t_{c}", label_visibility="collapsed")
            if t:
                out = out[s.astype(str).str.lower().str.contains(t.lower(), regex=False)]
        elif op == "is empty":
            out = out[s.isna() | (s.astype(str).str.strip() == "")]
        elif op == "has a value":
            out = out[s.notna() & (s.astype(str).str.strip() != "")]
        else:
            out = out[s.notna() & s.duplicated(keep=False)].sort_values(c)
    st.caption(f"Showing {len(out):,} of {len(df):,} rows")
    return out


SEVERITY_CSS = {"error": "color:#A32020;font-weight:600", "warning": "color:#9A5B00;font-weight:600", "info": "color:#52656B"}


def style_severity(df: pd.DataFrame):
    return df.style.map(lambda v: SEVERITY_CSS.get(v, ""), subset=["severity"]) if "severity" in df else df


def shade(v: float) -> str:
    """Coverage colour: grey = no such column, red -> amber -> green with % filled."""
    if pd.isna(v):
        return "background-color:#E9EDEE;color:#7A878C"
    lo, mid, hi = (214, 96, 77), (240, 201, 107), (62, 156, 110)
    a, b, t = (lo, mid, v / 50) if v < 50 else (mid, hi, (v - 50) / 50)
    rgb = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return f"background-color:rgb{rgb};color:#16262B"


def short_time(ts: str | None) -> str:
    if not ts:
        return "never"
    return pd.Timestamp(ts).strftime("%b %d, %H:%M UTC")
