"""Pipeline: sync sources -> build canonical records -> run all rules -> store issues and clusters."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .canon import build_all
from .connectors import build_connector
from .crossrules import cross_rules
from .mapping import suggest
from .models import Issue
from .resolve import resolve
from .rules import run_single_source
from .schema import ENTITIES
from .spelling import categorical_typos, fuzzy_duplicates
from .store import Store


def sync_source(store: Store, name: str, overrides: dict[str, Any] | None = None) -> dict[str, int | str]:
    """Fetch every selected entity of one source and save an immutable snapshot of each."""
    src = next((s for s in store.sources() if s["name"] == name), None)
    if not src:
        raise ValueError(f"Unknown source '{name}'")
    cfg = {**src["config"], **(overrides or {})}
    conn = build_connector(src["kind"], name, cfg)
    result: dict[str, int | str] = {}
    try:
        entities = src["entities"] or conn.list_entities()
    except Exception as e:  # noqa: BLE001  unreadable file, bad token, network down
        return {"(connection)": f"error: {e}"}
    for entity in entities:
        try:
            rows = list(conn.fetch(entity))
            store.save_snapshot(name, entity, rows)
            result[entity] = len(rows)
        except Exception as e:  # noqa: BLE001  one bad entity must not stop the others
            result[entity] = f"error: {e}"
    return result


def auto_map(store: Store, source: str, source_entity: str, canonical_entity: str, min_score: float = 85) -> dict[str, str]:
    """Save the suggested mapping (only confident suggestions) unless one already exists."""
    existing = store.get_mapping(source, source_entity)
    if existing:
        return existing["columns"]
    df = store.rows(source, source_entity)
    samples = {c: df[c].dropna().tolist()[:50] for c in df.columns}
    cols = {s.source_column: s.field for s in suggest(list(df.columns), canonical_entity, samples) if s.score >= min_score}
    store.save_mapping(source, source_entity, canonical_entity, cols)
    return cols


def validate(store: Store, expected: dict[str, list[str]] | None = None) -> dict[str, Any]:
    run_id = store.start_run()
    by_entity = build_all(store)
    issues: list[Issue] = []
    cluster_rows: list[tuple] = []
    candidates: list[dict] = []
    totals: dict[str, Any] = {}
    for entity, recs in by_entity.items():
        fields = ENTITIES[entity]
        sources = sorted({r.source for r in recs})
        issues += run_single_source(recs, fields)
        issues += categorical_typos(recs, fields)
        issues += fuzzy_duplicates(recs, fields)
        clusters, cands = resolve(entity, recs, fields)
        candidates += cands
        issues += cross_rules(entity, clusters, fields, (expected or {}).get(entity) or sources)
        for cl in clusters:
            for m in cl.members:
                cluster_rows.append((entity, cl.id, m.rid, cl.confidence, cl.reasons.get(m.rid, "")))
        totals[entity] = {"records": len(recs), "real_world_records": len(clusters), "sources": sources}
    # same finding can be produced twice (e.g. both rows of a duplicate pair): keep one per fingerprint
    unique = {i.fingerprint: i for i in issues}
    store.replace_clusters(run_id, cluster_rows)
    store.upsert_issues(run_id, list(unique.values()))
    summary = {"run_id": run_id, "entities": totals, "issues": len(unique),
               "by_rule": dict(Counter(i.rule for i in unique.values())),
               "by_severity": dict(Counter(i.severity for i in unique.values())),
               "review_candidates": sorted(candidates, key=lambda c: -c["score"])[:500]}
    store.finish_run(run_id, summary)
    return summary


def sync_and_validate(store: Store, overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """`overrides` = per-source config such as a pasted token (kept in memory, never written to the database)."""
    synced = {s["name"]: sync_source(store, s["name"], (overrides or {}).get(s["name"])) for s in store.sources()}
    for _, snap in store.latest_snapshots().iterrows():
        if not store.get_mapping(snap["source"], snap["entity"]):
            low = snap["entity"].lower()
            guess = "vehicle" if any(w in low for w in ("vehicle", "truck", "unit", "fleet")) else "driver"
            auto_map(store, snap["source"], snap["entity"], guess)
    summary = validate(store, json.loads(store.get_setting("expected_sources", "{}")) or None)
    summary["synced"] = synced
    return summary
