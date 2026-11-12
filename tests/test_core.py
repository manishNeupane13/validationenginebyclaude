import json

import httpx
import pandas as pd
import pytest

from dqp.connectors import ExcelConnector, MondayConnector, SamsaraConnector, StaticConnector, build_connector
from dqp.demo import load_demo
from dqp.engine import auto_map, sync_source, validate
from dqp.mapping import suggest
from dqp.normalize import normalize
from dqp.schema import field_map
from dqp.store import Store

D = field_map("driver")
V = field_map("vehicle")


# ---- normalization -------------------------------------------------------
def test_phone_formats_equal():
    a, b = normalize(D["phone"], "+1 (312) 555-0142"), normalize(D["phone"], "312.555.0142")
    assert a.norm == b.norm == "3125550142" and a.ok


def test_name_last_first_and_accents():
    assert normalize(D["full_name"], "García, José").norm == normalize(D["full_name"], "Jose Garcia").norm


def test_vin_and_placeholders():
    assert not normalize(V["vin"], "1HGBH41JXMN10918").ok            # 16 chars
    assert normalize(V["vin"], "1HGBH41JXMN109186").ok
    assert normalize(D["license_number"], "N/A").norm is None


def test_date_and_enum():
    assert normalize(D["hire_date"], "45000").norm == "2023-03-15"   # Excel serial
    assert not normalize(D["hire_date"], "31/31/2020").ok
    assert not normalize(D["status"], "Actve").ok


# ---- mapping -------------------------------------------------------------
def test_mapping_explains_and_is_one_to_one():
    s = {x.source_column: x for x in suggest(["Driver", "CDL #", "Mobile", "Start Date", "Notes"], "driver")}
    assert s["Driver"].field == "full_name" and s["CDL #"].field == "license_number"
    assert s["Mobile"].field == "phone" and s["Start Date"].field == "hire_date" and "Notes" not in s
    assert all(x.reason for x in s.values())


# ---- connectors ------------------------------------------------------------
def test_samsara_pagination_and_flatten():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        assert req.headers["authorization"] == "Bearer tok"
        if "after" not in req.url.params:
            return httpx.Response(200, json={"data": [{"id": "1", "name": "A", "externalIds": {"x": "9"}}],
                                             "pagination": {"hasNextPage": True, "endCursor": "c1"}})
        return httpx.Response(200, json={"data": [{"id": "1"}, {"id": "2", "name": "B"}],
                                         "pagination": {"hasNextPage": False, "endCursor": "c1"}})

    c = SamsaraConnector("sam", token="tok", transport=httpx.MockTransport(handler))
    rows = list(c.fetch("drivers"))
    assert [r["_key"] for r in rows] == ["1", "2"] and rows[0]["externalIds.x"] == "9" and len(calls) == 2


def test_samsara_retries_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}) if n["i"] == 1 else httpx.Response(200, json={"data": [], "pagination": {}})

    assert SamsaraConnector("s", token="t", transport=httpx.MockTransport(handler)).test_connection()[0]


def test_monday_cursor_paging_and_titles():
    def handler(req):
        q = json.loads(req.content)["query"]
        item = lambda i: {"id": i, "name": f"Driver {i}", "column_values": [{"id": "c1", "text": "D123456"}]}  # noqa: E731
        if "next_items_page" in q:
            return httpx.Response(200, json={"data": {"next_items_page": {"cursor": None, "items": [item("2")]}}})
        return httpx.Response(200, json={"data": {"boards": [{"columns": [{"id": "c1", "title": "CDL #"}],
                                                              "items_page": {"cursor": "abc", "items": [item("1")]}}]}})

    c = MondayConnector("m", boards={"drivers": 5}, token="t", transport=httpx.MockTransport(handler))
    rows = list(c.fetch("drivers"))
    assert [r["_key"] for r in rows] == ["1", "2"] and rows[0]["CDL #"] == "D123456" and rows[0]["Name"] == "Driver 1"


def test_missing_token_is_clear(monkeypatch):
    monkeypatch.delenv("SAMSARA_API_TOKEN", raising=False)
    ok, msg = SamsaraConnector("s").test_connection()
    assert not ok and "token" in msg.lower()


def test_excel_keeps_leading_zeros(tmp_path):
    p = tmp_path / "d.xlsx"
    pd.DataFrame({"CDL #": ["00123", None], "Driver": ["A B", "C D"]}).to_excel(p, index=False)
    rows = list(ExcelConnector("x", path=str(p)).fetch("Sheet1"))
    assert rows[0]["CDL #"] == "00123" and rows[1]["CDL #"] is None and rows[0]["_key"] == "r2"


def test_registry():
    assert isinstance(build_connector("static", "s", {"entities": {}}), StaticConnector)
    with pytest.raises(ValueError):
        build_connector("nope", "x", {})


# ---- store -------------------------------------------------------------------
def test_secrets_never_persisted(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.add_source("sam", "samsara", {"token": "SECRET", "token_env": "SAMSARA_API_TOKEN"})
    assert "SECRET" not in json.dumps(st.sources()) and st.sources()[0]["config"]["token_env"] == "SAMSARA_API_TOKEN"


# ---- full pipeline ---------------------------------------------------------
@pytest.fixture()
def demo(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    summary = load_demo(st, str(tmp_path / "demo"))
    return st, summary


def test_pipeline_finds_every_planted_defect(demo):
    st, s = demo
    by = s["by_rule"]
    assert s["entities"]["driver"]["real_world_records"] == 41 and s["entities"]["vehicle"]["real_world_records"] == 30
    for rule in ("missing", "format", "invalid_value", "duplicate", "spelling", "possible_duplicate", "missing_in_platform", "mismatch", "value_missing"):
        assert by.get(rule, 0) > 0, rule
    iss = st.issues()
    msgs = " ".join(iss.message)
    assert "Calros Taylor" in msgs and "'Actve'" in msgs and "Intrenational" in msgs
    assert "Zoe Ng" in " ".join(iss[iss.rule == "missing_in_platform"].message)


def test_phone_format_difference_is_not_a_mismatch(demo):
    st, _ = demo
    iss = st.issues()
    phone = iss[(iss.rule == "mismatch") & (iss.field == "phone")]
    assert all("+1" not in m for m in phone.message)


def test_key_collision_does_not_merge_two_people(demo):
    st, _ = demo
    cl = st.clusters()
    names = {"Victor Lee", "Hannah Clark"}
    from dqp.workspace import load_workspace
    ws = load_workspace(st)
    for c in ws.clusters["driver"]:
        found = {m.norm.get("full_name") for m in c.members if m.norm.get("full_name")}
        assert len({n for n in found if n in {"victor lee", "hannah clark", "lee victor", "clark hannah"}} & {"victor lee", "hannah clark"}) < 2


def test_triage_survives_rerun_and_autocloses(demo):
    st, _ = demo
    row = st.issues().iloc[0]
    st.set_status([row.fingerprint], "ignored", "known")
    validate(st)
    after = st.issues().set_index("fingerprint").loc[row.fingerprint]
    assert after.status == "ignored" and after.note == "known"
    # fix data in one source: its issues are auto-closed
    st.add_source("Cogoeld ELD", "static", {"entities": {"cogoeld_drivers": []}})
    sync_source(st, "Cogoeld ELD")
    validate(st)
    assert (st.issues().status == "fixed").any()


def test_sync_isolates_entity_errors(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    st.add_source("x", "excel", {"path": str(tmp_path / "missing.xlsx")})
    assert "error" in str(sync_source(st, "x"))


def test_is_in_lookup(demo):
    from dqp.lookup import is_in, search_rows
    from dqp.schema import ENTITIES
    from dqp.workspace import load_workspace
    st, _ = demo
    ws = load_workspace(st)
    res = is_in(["Zoe Ng", "ng, zoe", "Carlos Tayler", "Nobody Here"], ws.records["driver"], ENTITIES["driver"]).set_index("Searched for")
    assert res.loc["Zoe Ng", "Monday – New drivers"] and not res.loc["Zoe Ng", "Samsara"]
    assert res.loc["ng, zoe", "Found in"] == "1 of 4"             # "Last, First" normalizes
    assert "closest" in res.loc["Carlos Tayler", "Note"]          # typo gets a hint
    assert res.loc["Nobody Here", "Found in"] == "0 of 4"
    frames = {(s, e): st.rows(s, e) for s, e in st.latest_snapshots()[["source", "entity"]].itertuples(index=False)}
    assert len(search_rows(frames, "zoe")) == 1
