from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import coverage as cov
from ..connectors import available_kinds, build_connector
from ..engine import sync_source
from ..lookup import is_in, search_rows
from ..mapping import suggest
from ..schema import ENTITIES
from .common import (filter_df, open_issue_map, raw_frames, run_everything, scheduler, session_overrides, shade, short_time,
                     store, style_severity, version, workspace)

NOT_USED = "(not used)"


def _done(msg: str) -> None:
    st.cache_data.clear()
    st.toast(msg)
    st.rerun()


# =====================================================================================
# PLATFORM DATA  (one page per platform)
# =====================================================================================
def platform_page(source: str) -> None:
    s, v = store(), version()
    snaps = s.latest_snapshots()
    mine = snaps[snaps.source == source]
    src = next((x for x in s.sources() if x["name"] == source), None)
    st.title(source)
    if src is None:
        st.warning("This platform was removed. Pick another one in the sidebar.")
        return
    top = st.columns([3, 1])
    top[0].caption(f"{src['kind'].title()} connection · last synced {short_time(mine.fetched_at.max() if not mine.empty else None)}")
    if top[1].button("Sync this platform", key=f"sync_{source}", width="stretch"):
        with st.spinner("Fetching…"):
            res = sync_source(s, source, session_overrides().get(source))
        errors = {k: x for k, x in res.items() if isinstance(x, str)}
        if errors:
            st.error("; ".join(f"{k}: {x}" for k, x in errors.items()))
        else:
            _done("Synced. Run a check from Overview to refresh issues.")
    if mine.empty:
        st.info("No data yet. Select **Sync this platform**, or check the connection details under Platforms & schedule.")
        return
    frames, issues_by_rid = raw_frames(v), open_issue_map(source)
    tabs = st.tabs(mine.entity.tolist())
    for tab, ent in zip(tabs, mine.entity):
        with tab:
            df = frames[(source, ent)].copy()
            mapping = (s.get_mapping(source, ent) or {}).get("columns", {})
            df.insert(1, "Issues", [", ".join(sorted(issues_by_rid.get(f"{source}:{k}", []))) for k in df["_key"]])
            m = st.columns(4)
            m[0].metric("Rows", f"{len(df):,}")
            m[1].metric("Columns", len([c for c in df.columns if c not in ("_key", "Issues")]))
            m[2].metric("Mapped to common fields", len(mapping))
            m[3].metric("Rows with open issues", int((df["Issues"] != "").sum()))
            rows_tab, cols_tab = st.tabs(["Rows", "Columns"])
            with rows_tab:
                only_bad = st.toggle("Only rows with issues", key=f"bad_{source}_{ent}")
                view = df[df["Issues"] != ""] if only_bad else df
                out = filter_df(view, f"{source}_{ent}")
                st.dataframe(out.rename(columns={"_key": "Row"}), hide_index=True, width="stretch", height=480)
                st.download_button("Download these rows (CSV)", out.to_csv(index=False), f"{source}_{ent}.csv", key=f"dl_{source}_{ent}")
            with cols_tab:
                data = df.drop(columns=["Issues"])
                prof = []
                labels = {f.name: f.label for fl in ENTITIES.values() for f in fl}
                for c in data.columns:
                    if c == "_key":
                        continue
                    col = data[c]
                    filled = col.notna() & (col.astype(str).str.strip() != "")
                    top = col[filled].astype(str).value_counts().head(3)
                    prof.append({"Column": c, "Filled": int(filled.sum()), "Empty": int((~filled).sum()),
                                 "Filled %": round(100 * filled.mean(), 1), "Distinct": int(col[filled].nunique()),
                                 "Common field": labels.get(mapping.get(c, ""), "not mapped"),
                                 "Most common values": ", ".join(f"{k} ({n})" for k, n in top.items())})
                st.dataframe(pd.DataFrame(prof), hide_index=True, width="stretch",
                             column_config={"Filled %": st.column_config.ProgressColumn("Filled %", min_value=0, max_value=100, format="%.0f%%")})


# =====================================================================================
# VALIDATION & TOOLS
# =====================================================================================
def overview_page() -> None:
    s, v = store(), version()
    st.title("Overview")
    run = s.latest_run()
    head = st.columns([3, 1])
    head[0].caption(f"Last check: {short_time(run['finished_at']) if run else 'never'}")
    if head[1].button("Fetch and check everything", type="primary", width="stretch"):
        run_everything()
        st.rerun()
    if not run:
        st.info("Nothing has been checked yet. Add platforms under **Platforms & schedule**, or load the demo data there.")
        return
    ws, issues = workspace(v), s.issues()
    open_ = issues[issues.status == "open"]
    total = sum(len(r) for r in ws.records.values())
    bad = {rid for r in open_[open_.severity.isin(["error", "warning"])].record_ids for rid in json.loads(r)}
    clean = 100 * (1 - len(bad) / total) if total else 100
    m = st.columns(5)
    m[0].metric("Rows without errors or warnings", f"{clean:.0f}%")
    m[1].metric("Platforms", len(s.sources()))
    m[2].metric("Rows checked", f"{total:,}")
    m[3].metric("Open errors", int((open_.severity == "error").sum()))
    m[4].metric("Open warnings", int((open_.severity == "warning").sum()))
    left, right = st.columns(2)
    with left:
        st.subheader("Open issues by type")
        if open_.empty:
            st.success("No open issues.")
        else:
            st.bar_chart(open_.groupby("rule").size().sort_values(), horizontal=True, color="#0F6B63")
    with right:
        st.subheader("Open issues by platform")
        by = open_[open_.source != "multiple"].groupby("source").size()
        if not by.empty:
            st.bar_chart(by.sort_values(), horizontal=True, color="#0F6B63")
        st.caption(f"Plus {int((open_.source == 'multiple').sum())} issues that compare several platforms (see Compare records).")
    st.subheader("What each platform covers")
    for entity, info in run["summary"]["entities"].items():
        st.write(f"**{entity.title()}s:** {info['records']:,} rows across {len(info['sources'])} platforms, "
                 f"matched to **{info['real_world_records']:,}** distinct {entity}s.")
    cands = run["summary"].get("review_candidates", [])
    if cands:
        with st.expander(f"{len(cands)} possible matches to review (similar names, not merged automatically)"):
            st.dataframe(pd.DataFrame(cands).rename(columns={"a_label": "Record A", "b_label": "Record B", "score": "Similarity %"})
                         [["entity", "Record A", "Record B", "Similarity %", "a", "b"]], hide_index=True, width="stretch")


def issues_page() -> None:
    s = store()
    st.title("Issues")
    df = s.issues()
    if df.empty:
        st.info("No issues yet. Run a check from Overview.")
        return
    c = st.columns(5)
    status = c[0].multiselect("Status", ["open", "ignored", "fixed"], default=["open"])
    sev = c[1].multiselect("Severity", ["error", "warning", "info"], default=["error", "warning", "info"])
    rule = c[2].multiselect("Type", sorted(df.rule.unique()), placeholder="All types")
    ent = c[3].multiselect("Record type", sorted(df.entity.unique()), placeholder="All")
    src = c[4].multiselect("Platform", sorted(df.source.unique()), placeholder="All")
    view = df[df.status.isin(status) & df.severity.isin(sev)]
    for col, vals in (("rule", rule), ("entity", ent), ("source", src)):
        if vals:
            view = view[view[col].isin(vals)]
    view = filter_df(view, "issues", "Search messages, values, names")
    cols = ["severity", "rule", "entity", "source", "field", "value", "message", "status", "note", "last_seen"]
    ev = st.dataframe(style_severity(view[cols]), hide_index=True, width="stretch", height=420, on_select="rerun",
                      selection_mode="multi-row", key="issues_grid")
    picked = view.iloc[ev.selection.rows] if ev.selection.rows else view.iloc[0:0]
    st.download_button("Download these issues (CSV)", view[cols].to_csv(index=False), "issues.csv")
    if picked.empty:
        st.caption("Select one or more rows to mark them fixed, ignore them or reopen them.")
        return
    st.subheader(f"{len(picked)} selected")
    note = st.text_input("Note (optional)", key="triage_note", placeholder="Why this is fixed or ignored")
    b = st.columns(4)
    for col, label, new in ((b[0], "Mark fixed", "fixed"), (b[1], "Ignore", "ignored"), (b[2], "Reopen", "open")):
        if col.button(label, key=f"tri_{new}", width="stretch"):
            s.set_status(picked.fingerprint.tolist(), new, note)
            _done(f"{len(picked)} issue(s) set to {new}")
    if len(picked) == 1:
        r = picked.iloc[0]
        st.markdown(f"**{r.message}**")
        st.write(f"Expected: {r.expected or '—'}  ·  First seen {short_time(r.first_seen)}")
        if r.source == "multiple":
            st.caption(f"This compares several platforms. Open **Compare records** and search for {json.loads(r.details).get('label', r.record_key)}.")
        with st.expander("Details"):
            st.json(json.loads(r.details))


def compare_page() -> None:
    s, v = store(), version()
    st.title("Compare records")
    ws = workspace(v)
    if not ws.clusters:
        st.info("No matched records yet. Run a check from Overview.")
        return
    entity = st.radio("Record type", list(ws.clusters), horizontal=True, format_func=lambda e: e.title() + "s", key="cmp_entity")
    clusters = ws.clusters[entity]
    sources = sorted({r.source for r in ws.records[entity]})
    table = cov.presence_table(clusters, sources)
    text = {cl.id: " ".join(str(x) for m in cl.members for x in m.raw.values() if x is not None).lower() for cl in clusters}
    table["_text"] = table["Cluster"].map(text)
    c = st.columns([3, 2, 2, 2])
    q = c[0].text_input("Search", key="cmp_q", placeholder="Name, ID, VIN, license, phone…")
    present = c[1].multiselect("Is in", sources, key="cmp_in", placeholder="All platforms")
    absent = c[2].multiselect("Is not in", sources, key="cmp_out", placeholder="None")
    issues = s.issues()
    flagged = set(issues[(issues.status == "open") & (issues.source == "multiple")].record_key)
    only_diff = c[3].toggle("Only with differences", key="cmp_diff")
    view = table
    if q.strip():
        view = view[view["_text"].str.contains(q.strip().lower(), regex=False)]
    for p in present:
        view = view[view[p]]
    for a in absent:
        view = view[~view[a]]
    if only_diff:
        view = view[view["Cluster"].isin(flagged)]
    st.caption(f"{len(view):,} of {len(table):,} {entity}s")
    shown = view.drop(columns=["_text"]).reset_index(drop=True)
    ev = st.dataframe(shown, hide_index=True, width="stretch", height=300, on_select="rerun", selection_mode="single-row",
                      key=f"cmp_grid_{entity}",
                      column_config={p: st.column_config.CheckboxColumn(p) for p in sources})
    if not ev.selection.rows:
        st.caption("Select a row to see every platform's version side by side.")
        return
    cid = shown.iloc[ev.selection.rows[0]]["Cluster"]
    cl = next(x for x in clusters if x.id == cid)
    st.subheader(cl.label())
    rows = []
    for f in ENTITIES[entity]:
        per = {src: " / ".join(sorted({str(m.raw[f.name]) for m in cl.members if m.source == src and m.norm.get(f.name)})) or "—"
               for src in sources}
        norms = {m.norm[f.name] for m in cl.members if m.norm.get(f.name)}
        have = [src for src in sources if per[src] != "—"]
        state = "Differs" if len(norms) > 1 else ("Match" if len(have) > 1 else ("Only one platform" if have else "Empty everywhere"))
        rows.append({"Field": f.label, **per, "Result": state})
    comp = pd.DataFrame(rows)
    st.dataframe(comp.style.apply(lambda r: ["background-color:#FBE9C8" if r["Result"] == "Differs" else "" for _ in r], axis=1),
                 hide_index=True, width="stretch")
    with st.expander("How these rows were matched"):
        st.dataframe(pd.DataFrame([{"Row": m.rid, "Matched because": cl.reasons.get(m.rid, ""), "Confidence": f"{cl.confidence:.0%}"}
                                   for m in cl.members]), hide_index=True, width="stretch")
    rids = {m.rid for m in cl.members}
    mine = issues[(issues.status == "open") & (issues.record_key.eq(cl.id) | issues.record_ids.apply(lambda x: bool(rids & set(json.loads(x)))))]
    if not mine.empty:
        st.markdown("**Open issues for this record**")
        st.dataframe(style_severity(mine[["severity", "rule", "source", "message"]]), hide_index=True, width="stretch")


def find_page() -> None:
    s, v = store(), version()
    st.title("Find & look up")
    ws = workspace(v)
    t1, t2 = st.tabs(["Search every platform", "Is it in…? (paste a list)"])
    with t1:
        q = st.text_input("Search all platforms at once", key="find_q", placeholder="Any name, ID, VIN, plate, email or phone fragment")
        if q.strip():
            res = search_rows(raw_frames(v), q)
            if res.empty:
                st.info("No row in any platform contains that text. Try a shorter piece, or use the lookup tab, which also forgives typos.")
            else:
                counts = res.groupby("Platform").size()
                cols = st.columns(len(counts))
                for col, (p, n) in zip(cols, counts.items()):
                    col.metric(p, n)
                plats = st.multiselect("Show only", sorted(counts.index), key="find_p", placeholder="All platforms")
                st.dataframe(res[res.Platform.isin(plats)] if plats else res, hide_index=True, width="stretch")
    with t2:
        if not ws.records:
            st.info("Run a check first so there is data to look in.")
            return
        entity = st.radio("Looking for", list(ws.records), horizontal=True, format_func=lambda e: e.title() + "s", key="look_entity")
        raw = st.text_area("One per line: names, VINs, license numbers, employee IDs, emails or phone numbers", height=150, key="look_text")
        vals = [x for x in raw.splitlines() if x.strip()]
        if vals:
            res = is_in(vals, ws.records[entity], ENTITIES[entity])
            st.dataframe(res, hide_index=True, width="stretch", column_config={
                c: st.column_config.CheckboxColumn(c) for c in res.columns if c in {r.source for r in ws.records[entity]}})
            missing = res[res["Found in"].str.startswith("0 ")]
            if not missing.empty:
                st.warning(f"{len(missing)} value(s) were not found anywhere.")
            st.download_button("Download results (CSV)", res.to_csv(index=False), "lookup.csv")


def coverage_page() -> None:
    s, v = store(), version()
    st.title("Coverage")
    ws = workspace(v)
    if not ws.records:
        st.info("Run a check from Overview first.")
        return
    entity = st.radio("Record type", list(ws.records), horizontal=True, format_func=lambda e: e.title() + "s", key="cov_entity")
    recs, fields = ws.records[entity], ENTITIES[entity]
    sources = sorted({r.source for r in recs})
    t1, t2, t3, t4 = st.tabs(["Which data is where", "Which records are where", "Column names", "Column profile"])
    with t1:
        st.caption("Share of rows that have a value. Grey means that platform has no column for the field at all.")
        fc = cov.field_coverage(recs, fields)
        st.dataframe(fc.style.map(shade).format(lambda x: "no column" if pd.isna(x) else f"{x:.0f}%"), width="stretch")
        st.dataframe(cov.record_counts(recs), hide_index=True)
    with t2:
        clusters = ws.clusters.get(entity, [])
        combos = cov.entity_presence(clusters, sources)
        m = st.columns(len(sources))
        for col, src in zip(m, sources):
            only = sum(1 for cl in clusters if cl.sources == {src})
            col.metric(src, sum(1 for cl in clusters if src in cl.sources), f"{only} only here", delta_color="off")
        if not combos.empty:
            st.bar_chart(combos.set_index("Platforms")["Records"], horizontal=True, color="#0F6B63")
            st.dataframe(combos, hide_index=True, width="stretch",
                         column_config={p: st.column_config.CheckboxColumn(p) for p in sources})
    with t3:
        st.caption("Which column in each platform feeds each common field.")
        st.dataframe(cov.column_matrix(s.mappings(), entity, fields), hide_index=True, width="stretch")
    with t4:
        pick = st.selectbox("Platform", sources, key="prof_src")
        st.dataframe(cov.column_stats(recs, fields, pick), hide_index=True, width="stretch",
                     column_config={"Filled %": st.column_config.ProgressColumn("Filled %", min_value=0, max_value=100, format="%.0f%%")})


def mapping_page() -> None:
    s, v = store(), version()
    st.title("Column mapping")
    st.caption("Tell the system which column in each platform means what. Suggestions come with a reason. Nothing is saved until you select Save.")
    snaps = s.latest_snapshots()
    if snaps.empty:
        st.info("Sync at least one platform first.")
        return
    label = lambda r: f"{r.source} › {r.entity}"  # noqa: E731
    options = {label(r): (r.source, r.entity) for r in snaps.itertuples()}
    choice = st.selectbox("Platform and sheet", list(options), key="map_pick")
    source, ent = options[choice]
    saved = s.get_mapping(source, ent)
    guess = "vehicle" if any(w in ent.lower() for w in ("vehicle", "truck", "unit", "fleet")) else "driver"
    canon = st.radio("This data describes", list(ENTITIES), index=list(ENTITIES).index(saved["canonical_entity"] if saved else guess),
                     horizontal=True, format_func=lambda e: e.title() + "s", key=f"map_ent_{choice}")
    df = raw_frames(v)[(source, ent)]
    cols = [c for c in df.columns if not c.startswith("_")]
    sug = {x.source_column: x for x in suggest(cols, canon, {c: df[c].dropna().tolist()[:50] for c in cols})}
    current = saved["columns"] if saved and saved["canonical_entity"] == canon else {c: x.field for c, x in sug.items() if x.score >= 85}
    labels = {f.name: f.label for f in ENTITIES[canon]}
    rows = [{"Column": c, "Maps to": current.get(c, NOT_USED), "Suggested": sug[c].field if c in sug else "",
             "Why": sug[c].reason if c in sug else "", "Examples": ", ".join(str(x) for x in df[c].dropna().astype(str).head(3))} for c in cols]
    edited = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", key=f"map_ed_{choice}_{canon}",
                            disabled=["Column", "Suggested", "Why", "Examples"],
                            column_config={"Maps to": st.column_config.SelectboxColumn("Maps to", options=[NOT_USED] + list(labels),
                                                                                       help="; ".join(f"{k} = {x}" for k, x in labels.items()), required=True)})
    chosen = {r["Column"]: r["Maps to"] for _, r in edited.iterrows() if r["Maps to"] != NOT_USED}
    dupes = pd.Series(list(chosen.values())).value_counts()
    dupes = dupes[dupes > 1]
    if not dupes.empty:
        st.error("Each common field can be fed by one column only. Used more than once: " + ", ".join(dupes.index))
    unmapped_required = [f.label for f in ENTITIES[canon] if f.required and f.name not in chosen.values()]
    if unmapped_required:
        st.warning("Required fields with no column: " + ", ".join(unmapped_required))
    if st.button("Save mapping", type="primary", disabled=not dupes.empty):
        s.save_mapping(source, ent, canon, chosen)
        _done("Mapping saved. Run a check from Overview to apply it.")


def sources_page() -> None:
    s = store()
    st.title("Platforms & schedule")
    srcs = s.sources()
    t1, t2, t3 = st.tabs(["Platforms", "Add a platform", "Schedule & settings"])
    with t1:
        if not srcs:
            st.info("No platforms yet. Add one, or load sample data to explore the app.")
            if st.button("Load demo data", type="primary"):
                from ..demo import load_demo
                with st.spinner("Building demo files and running checks…"):
                    load_demo(s)
                _done("Demo data loaded")
        snaps = s.latest_snapshots()
        for src in srcs:
            mine = snaps[snaps.source == src["name"]]
            with st.container(border=True):
                a, b, c, d = st.columns([3, 2, 1, 1])
                a.markdown(f"**{src['name']}**  \n{src['kind']}")
                b.caption(f"{int(mine.row_count.sum()):,} rows in {len(mine)} sheet(s) · synced {short_time(mine.fetched_at.max() if not mine.empty else None)}")
                if c.button("Test", key=f"t_{src['name']}", width="stretch"):
                    conn = build_connector(src["kind"], src["name"], {**src["config"], **session_overrides().get(src["name"], {})})
                    ok, msg = conn.test_connection()
                    (st.success if ok else st.error)(msg)
                if d.button("Remove", key=f"rm_{src['name']}", width="stretch"):
                    s.delete_source(src["name"])
                    _done(f"Removed {src['name']}")
                if src["kind"] in ("samsara", "monday"):
                    tok = st.text_input(f"Token for this session ({src['name']})", type="password", key=f"tok_{src['name']}",
                                        help="Kept in memory only. For scheduled runs set the environment variable instead.")
                    if tok:
                        session_overrides()[src["name"]] = {"token": tok}
        if srcs and st.button("Fetch and check everything", type="primary"):
            summary = run_everything()
            bad = {k: v for k, v in summary["synced"].items() if any(isinstance(x, str) for x in v.values())}
            (st.warning if bad else st.success)(f"Check finished: {summary['issues']} issues." + (f" Some platforms had errors: {bad}" if bad else ""))
    with t2:
        kind = st.selectbox("Platform type", [k for k in available_kinds() if k != "static"],
                            format_func=lambda k: {"excel": "Excel / CSV file", "samsara": "Samsara", "monday": "Monday.com"}.get(k, k))
        name = st.text_input("Name shown in the app", placeholder="e.g. Samsara, Monday – New drivers")
        cfg: dict = {}
        if kind == "excel":
            up = st.file_uploader("Excel or CSV file", type=["xlsx", "xls", "csv", "tsv"])
            path = st.text_input("…or a file path on this machine", placeholder="data/drivers.xlsx")
            if up:
                Path("data/uploads").mkdir(parents=True, exist_ok=True)
                path = f"data/uploads/{up.name}"
                Path(path).write_bytes(up.getvalue())
            cfg = {"path": path}
        elif kind == "samsara":
            cfg = {"token_env": st.text_input("Environment variable holding the API token", "SAMSARA_API_TOKEN"),
                   "base_url": st.text_input("API base URL", "https://api.samsara.com")}
            st.caption("Reads vehicles and drivers.")
        elif kind == "monday":
            cfg = {"token_env": st.text_input("Environment variable holding the API token", "MONDAY_API_TOKEN")}
            boards = st.text_area("Boards: one per line as  name=board id", placeholder="new_drivers=1234567890")
            cfg["boards"] = {l.split("=")[0].strip(): l.split("=")[1].strip() for l in boards.splitlines() if "=" in l}
        if st.button("Add platform", type="primary", disabled=not name.strip()):
            conn = build_connector(kind, name.strip(), cfg)
            ok, msg = conn.test_connection()
            if not ok and kind == "excel":
                st.error(msg)
            else:
                s.add_source(name.strip(), kind, cfg)
                if not ok:
                    st.warning(msg + " The platform was added; add the token on the Platforms tab before syncing.")
                else:
                    _done(f"Added {name.strip()}. Open it in the sidebar and select Sync.")
    with t3:
        sch = scheduler()
        st.subheader("Automatic runs")
        mins = st.number_input("Fetch and check every (minutes)", 1, 1440, int(s.get_setting("interval_min", "60")))
        c = st.columns(3)
        if c[0].button("Start schedule", disabled=sch.running):
            s.set_setting("interval_min", str(mins))
            sch.start(mins)
            st.rerun()
        if c[1].button("Stop schedule", disabled=not sch.running):
            sch.stop()
            st.rerun()
        st.caption(("Running every %d min while this app is open. " % sch.interval if sch.running else "Not running. ")
                   + ("Last error: " + sch.last_error if sch.last_error else "")
                   + " For an always-on server use `dqp run --every 60` or cron instead.")
        st.subheader("Which platforms should hold every record?")
        st.caption("A driver or vehicle missing from one of these is reported. Leave empty to expect all platforms that have that kind of data.")
        expected = json.loads(s.get_setting("expected_sources", "{}") or "{}")
        ws = workspace(version())
        for entity, recs in ws.records.items():
            allsrc = sorted({r.source for r in recs})
            expected[entity] = st.multiselect(f"{entity.title()}s should be in", allsrc, default=[x for x in expected.get(entity, []) if x in allsrc], key=f"exp_{entity}")
        if st.button("Save settings"):
            s.set_setting("expected_sources", json.dumps({k: v for k, v in expected.items() if v}))
            _done("Saved. Takes effect on the next check.")
