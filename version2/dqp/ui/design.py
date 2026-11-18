"""Shared presentation for the Streamlit workspace."""

from html import escape

import streamlit as st


def apply_theme() -> None:
    st.markdown("""
<style>
.block-container { max-width: 1440px; padding-top: 3.25rem; padding-bottom: 4rem; }
[data-testid="stSidebar"] { border-right: 1px solid #dce6eb; }
[data-testid="stSidebarNavLink"] { border-radius: .55rem; }
[data-testid="stSidebarNavLink"][aria-current="page"] {
    background: #dcefeb; color: #105b52; font-weight: 650;
}
[data-testid="stMetric"] {
    background: #fff; border: 1px solid #dce6eb; border-radius: .9rem;
    padding: .9rem 1rem; box-shadow: 0 2px 4px #15344908; height: 100%;
}
[data-testid="stMetricLabel"] { color: #4b626f; }
[data-testid="stMetricValue"] { font-weight: 700; letter-spacing: -.045em; }
[data-testid="stVerticalBlockBorderWrapper"] > div { background: #fff; }
[data-testid="stDataFrame"], [data-testid="stFileUploaderDropzone"] { border-radius: .75rem; overflow: hidden; }
[data-testid="stTabs"] [role="tablist"] { gap: .65rem; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { font-weight: 700; }
h1, h2, h3 { letter-spacing: -.035em; }
.dqp-hero {
    padding: clamp(1.4rem, 4vw, 2.9rem);
    margin-bottom: .7rem; border-radius: 1.1rem;
    background: radial-gradient(ellipse at 90% 5%, #237969 0%, transparent 52%),
                linear-gradient(118deg, #132b3b, #164d4e);
    color: #fff;
}
.dqp-hero h1 {
    color: inherit; padding: 0; margin: 0 0 .5rem;
    font-size: clamp(1.8rem, 4vw, 2.8rem); line-height: 1.12;
}
.dqp-hero p { color: #e0f0ef; margin: 0; max-width: 58ch; }
</style>
""", unsafe_allow_html=True)


def header(title: str, description: str) -> None:
    st.markdown(
        f'<header class="dqp-hero"><h1>{escape(title)}</h1><p>{escape(description)}</p></header>',
        unsafe_allow_html=True,
    )
