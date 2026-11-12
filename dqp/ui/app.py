"""Run with:  streamlit run dqp/ui/app.py"""
import re

import streamlit as st

from dqp.ui import pages
from dqp.ui.common import store

st.set_page_config(page_title="Fleet data check", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
[data-testid="stMetricValue"]{font-weight:600}
.block-container{padding-top:2.2rem;max-width:1400px}
h1{letter-spacing:-0.01em}
</style>""", unsafe_allow_html=True)


def _platform(name: str):
    def _page():
        pages.platform_page(name)
    return _page


sources = store().sources()
platform_pages = [st.Page(_platform(s["name"]), title=s["name"], url_path="p-" + re.sub(r"[^a-z0-9]+", "-", s["name"].lower()).strip("-"))
                  for s in sources]
tools = [
    st.Page(pages.overview_page, title="Overview", url_path="overview", default=bool(sources)),
    st.Page(pages.issues_page, title="Issues", url_path="issues"),
    st.Page(pages.compare_page, title="Compare records", url_path="compare"),
    st.Page(pages.find_page, title="Find & look up", url_path="find"),
    st.Page(pages.coverage_page, title="Coverage", url_path="coverage"),
    st.Page(pages.mapping_page, title="Column mapping", url_path="mapping"),
    st.Page(pages.sources_page, title="Platforms & schedule", url_path="platforms", default=not sources),
]
nav = {"Platform data": platform_pages, "Checks & tools": tools} if platform_pages else {"Checks & tools": tools}
st.navigation(nav).run()
