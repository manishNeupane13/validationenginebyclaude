"""Smoke tests: every page renders against the demo data without raising."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dqp.demo import load_demo
from dqp.store import Store

PAGES = ["overview_page", "issues_page", "compare_page", "find_page", "coverage_page", "mapping_page", "sources_page"]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("ui")
    path = str(d / "ui.db")
    load_demo(Store(path), str(d / "demo"))
    return path


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(db, monkeypatch, page):
    monkeypatch.setenv("DQP_DB", db)
    at = AppTest.from_string(f"from dqp.ui import pages\npages.{page}()", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]


def test_platform_page_and_filters(db, monkeypatch):
    monkeypatch.setenv("DQP_DB", db)
    at = AppTest.from_string("from dqp.ui import pages\npages.platform_page('Samsara')", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    at.text_input(key="Samsara_drivers_q").set_value("garcia").run()
    assert not at.exception and any("Showing" in c.value for c in at.caption)


def test_app_entrypoint(db, monkeypatch):
    monkeypatch.setenv("DQP_DB", db)
    at = AppTest.from_file(str(Path(__file__).parent.parent / "dqp" / "ui" / "app.py"), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
