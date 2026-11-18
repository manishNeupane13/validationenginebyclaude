import json

import httpx
import pytest

from dqp.demo import load_demo
from dqp.engine import validate
from dqp.llm import MockLLM, OpenAICompatClient, ReviewQueue
from dqp.llm.client import parse_json
from dqp.llm.tasks import judge_match, mask, review_issue
from dqp.models import CanonRecord
from dqp.resolve import resolve
from dqp.schema import ENTITIES, field_map
from dqp.store import Store
from dqp.workspace import load_workspace


@pytest.fixture()
def demo(tmp_path):
    st = Store(str(tmp_path / "t.db"))
    load_demo(st, str(tmp_path / "demo"))
    return st


def rec(source, key, name, **raw):
    from dqp.normalize import normalize
    fm = field_map("driver")
    raw = {"full_name": name, **raw}
    norm = {k: normalize(fm[k], v).norm for k, v in raw.items()}
    return CanonRecord(source, "driver", key, raw, norm, {})


def find(ws, label):
    return next(r for r in ws.records["driver"] if r.label() == label)


# ---- client ---------------------------------------------------------------------------------
def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_client_retries_bad_json_and_flags_remote():
    replies = iter(["not json", '{"verdict": "same"}'])

    def handler(req):
        body = json.loads(req.content)
        assert body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    c = OpenAICompatClient(model="m", transport=httpx.MockTransport(handler))
    assert c.complete_json("s", "u") == {"verdict": "same"} and c.is_local
    assert not OpenAICompatClient("https://api.example.com/v1").is_local


# ---- tasks ----------------------------------------------------------------------------------
def test_judge_match_validates_clamps_and_caches(demo):
    ws = load_workspace(demo)
    a, b = find(ws, "Zoe Ng"), ws.records["driver"][0]
    llm = MockLLM(lambda s, u: {"verdict": "Different", "confidence": 7, "reason": "Names and licenses differ."})
    op = judge_match(llm, a, b, demo)
    assert (op.verdict, op.confidence) == ("different", 1.0)
    judge_match(llm, a, b, demo)
    assert len(llm.calls) == 1                      # second call served from cache


def test_unusable_answers_become_unsure_not_errors(demo):
    ws = load_workspace(demo)
    llm = MockLLM(lambda s, u: {"verdict": "maybe?!"})
    op = judge_match(llm, ws.records["driver"][0], ws.records["driver"][1], demo)
    assert op.verdict == "unsure" and op.confidence == 0 and len(llm.calls) == 2


def test_invented_identifier_is_dropped():
    iss = {"entity": "driver", "field": "license_number", "rule": "mismatch", "value": "A | B", "expected": "x", "message": "differs",
           "details": json.dumps({"by_source": {"excel": "D1234567", "samsara": "D1234576"}})}
    liar = MockLLM(lambda s, u: {"verdict": "real_problem", "confidence": .9, "suggested_value": "Z9999999", "reason": "r"})
    assert "suggested_value" not in review_issue(liar, iss, []).extra
    honest = MockLLM(lambda s, u: {"verdict": "real_problem", "confidence": .9, "suggested_value": "D1234567", "correct_source": "excel", "reason": "r"})
    assert review_issue(honest, iss, []).extra["suggested_value"] == "D1234567"


def test_masking():
    assert mask("(312) 555-0142", "phone") == "***-***-0142" and mask("jo@x.com", "email") == "j***@x.com"


# ---- human decisions change resolution ------------------------------------------------------------
def test_decisions_override_automatic_matching():
    fields = ENTITIES["driver"]
    a, b = rec("excel", "1", "John Smith"), rec("samsara", "9", "Jon Smith")
    auto, _ = resolve("driver", [a, b], fields)
    assert len(auto) == 1
    split, cands = resolve("driver", [a, b], fields, {frozenset((a.rid, b.rid)): "different"})
    assert len(split) == 2 and not cands
    c, d = rec("excel", "2", "Maria Lopez"), rec("samsara", "3", "Anna Kowalski")
    assert len(resolve("driver", [c, d], fields, {frozenset((c.rid, d.rid)): "same"})[0]) == 1


# ---- queue end to end ---------------------------------------------------------------------------
def test_match_review_flow_and_undo(demo):
    ws = load_workspace(demo)
    zoe, other = find(ws, "Zoe Ng"), find(ws, "James Smith")
    n0 = len(ws.clusters["driver"])
    q = ReviewQueue(demo)
    q.enqueue_matches([{"entity": "driver", "a": zoe.rid, "b": other.rid, "score": 88}])
    q.enqueue_matches([{"entity": "driver", "a": other.rid, "b": zoe.rid, "score": 88}])   # same pair, other order
    assert len(q.items("match", "pending")) == 1
    llm = MockLLM(lambda s, u: {"verdict": "same", "confidence": .8, "reason": "test"})
    assert q.analyze(llm)["analyzed"] == 1
    item = q.items("match", "pending").iloc[0]
    assert item.llm_verdict == "same" and item.status == "pending"        # advisory only: nothing applied yet
    validate(demo)
    assert len(load_workspace(demo).clusters["driver"]) == n0
    q.decide(int(item.id), "same", "checked by hand")
    validate(demo)
    assert len(load_workspace(demo).clusters["driver"]) == n0 - 1
    q.undo(int(item.id))
    validate(demo)
    assert len(load_workspace(demo).clusters["driver"]) == n0


def test_auto_threshold_applies_only_confident_answers(demo):
    ws = load_workspace(demo)
    zoe, o1, o2 = find(ws, "Zoe Ng"), find(ws, "James Smith"), find(ws, "Maria Johnson")
    q = ReviewQueue(demo)
    q.enqueue_matches([{"entity": "driver", "a": zoe.rid, "b": o1.rid, "score": 88}, {"entity": "driver", "a": zoe.rid, "b": o2.rid, "score": 87}])
    answers = iter([{"verdict": "different", "confidence": .99, "reason": "r"}, {"verdict": "same", "confidence": .6, "reason": "r"}])
    res = q.analyze(MockLLM(lambda s, u: next(answers)), auto_threshold=0.95)
    assert res["auto"] == 1 and q.counts()["pending"] == 1
    assert (q.items(status="auto").decision == "different").all()


def test_issue_review_flow(demo):
    q = ReviewQueue(demo)
    assert q.enqueue_issues(("spelling",)) > 0
    llm = MockLLM(lambda s, u: {"verdict": "real_problem", "confidence": .9, "suggested_value": None, "reason": "typo"})
    assert q.analyze(llm, limit=50)["analyzed"] > 0
    items = q.items("issue", "pending")
    fp = json.loads(items.iloc[0].payload)["fingerprint"]
    with pytest.raises(ValueError):
        q.decide(int(items.iloc[0].id), "accept_fix", "", "")                   # a fix needs a value
    q.decide(int(items.iloc[0].id), "accept_fix", "ok", "Ford")
    fx = q.approved_fixes()
    assert fx.iloc[0].new_value == "Ford" and fx.iloc[0].fingerprint == fp
    q.decide(int(items.iloc[1].id), "false_alarm")
    fp2 = json.loads(items.iloc[1].payload)["fingerprint"]
    assert demo.issues().set_index("fingerprint").loc[fp2].status == "ignored"
    q.undo(int(items.iloc[1].id))
    assert demo.issues().set_index("fingerprint").loc[fp2].status == "open"


def test_model_down_is_reported_not_fatal(demo):
    q = ReviewQueue(demo)
    q.enqueue_issues(("spelling",))

    def boom(s, u):
        raise httpx.ConnectError("refused")

    total = len(q.items("issue", "pending"))
    res = q.analyze(MockLLM(boom), limit=50)
    assert total > 0 and res["analyzed"] == total and q.counts()["with_ai"] == total   # shown as 'unsure', never lost


def test_report(demo):
    llm = MockLLM(lambda s, u: {"report": "**What looks healthy** all good"})
    assert "healthy" in ReviewQueue(demo).report(llm) and "healthy" in demo.get_setting("last_ai_report")
    assert "records_per_type" in llm.calls[0][1]
