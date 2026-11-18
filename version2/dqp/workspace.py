"""Everything the UI needs, assembled once per data version."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .canon import build_all
from .models import CanonRecord
from .resolve import Cluster
from .store import Store


@dataclass
class Workspace:
    records: dict[str, list[CanonRecord]]
    clusters: dict[str, list[Cluster]] = field(default_factory=dict)


def load_workspace(store: Store) -> Workspace:
    records = build_all(store)
    by_rid = {r.rid: r for rs in records.values() for r in rs}
    df: pd.DataFrame = store.clusters()
    clusters: dict[str, list[Cluster]] = {}
    for (entity, cid), grp in df.groupby(["entity", "cluster_id"], sort=False):
        members = [by_rid[r] for r in grp["rid"] if r in by_rid]
        if members:
            clusters.setdefault(entity, []).append(
                Cluster(cid, entity, members, float(grp["confidence"].iloc[0]), dict(zip(grp["rid"], grp["reason"]))))
    return Workspace(records, clusters)
