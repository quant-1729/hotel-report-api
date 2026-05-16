"""
Streamlit UI for the Hotel AI Report Generator.

Run with:
    cd project
    streamlit run app.py
"""

import csv
import io
import logging
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

# ── project path ──────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

logging.basicConfig(level=logging.ERROR)  # keep console clean in UI mode

from utils import generate_pdf, render_template, sanitize_filename  # noqa: E402

# ── constants ─────────────────────────────────────────────────────────────────
TEMPLATES_DIR  = PROJECT_DIR / "templates"
OUTPUT_DIR     = PROJECT_DIR / "output"
DEFAULT_CSV    = PROJECT_DIR / "data.csv"
FILENAME_COL   = "hotel_name"

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Hotel AI Report Generator",
    page_icon="🏨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* ── Sidebar ──────────────────────────────────────────────── */
  [data-testid="stSidebar"] {
    background: #0f172a;
  }
  [data-testid="stSidebar"] * {
    color: #e2e8f0 !important;
  }
  [data-testid="stSidebar"] .stMarkdown h1,
  [data-testid="stSidebar"] .stMarkdown h2,
  [data-testid="stSidebar"] .stMarkdown h3 {
    color: #ffffff !important;
  }
  [data-testid="stSidebar"] hr {
    border-color: #1e293b !important;
  }

  /* ── Main content ─────────────────────────────────────────── */
  .block-container {
    padding-top: 2rem;
    max-width: 1100px;
  }

  /* ── Stat cards ───────────────────────────────────────────── */
  .stat-row {
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
  }
  .stat-card {
    flex: 1;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 18px 20px;
  }
  .stat-card .label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #94a3b8;
    margin-bottom: 4px;
  }
  .stat-card .value {
    font-size: 28px;
    font-weight: 900;
    color: #0f172a;
    line-height: 1;
  }
  .stat-card .value.green  { color: #059669; }
  .stat-card .value.red    { color: #dc2626; }
  .stat-card .value.blue   { color: #3525cd; }

  /* ── Result rows ──────────────────────────────────────────── */
  .result-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border-radius: 8px;
    margin-bottom: 6px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
  }
  .result-row.failed {
    background: #fff1f2;
    border-color: #fecdd3;
  }
  .result-row .filename {
    flex: 1;
    font-size: 13px;
    font-weight: 600;
    color: #1e293b;
    font-family: monospace;
  }
  .result-row .err-msg {
    font-size: 11px;
    color: #dc2626;
  }
  .badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 20px;
  }
  .badge.ok  { background: #d1fae5; color: #065f46; }
  .badge.err { background: #fee2e2; color: #991b1b; }

  /* ── Section headers ──────────────────────────────────────── */
  .section-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #94a3b8;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #e2e8f0;
  }

  /* ── Generate button ──────────────────────────────────────── */
  [data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #3525cd, #6d5cf7);
    border: none;
    font-weight: 700;
    font-size: 15px;
    letter-spacing: 0.02em;
    height: 52px;
  }
  [data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #2a1fb0, #5b4ae8);
  }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _read_csv_upload(uploaded_file) -> list[dict]:
    content = uploaded_file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    return [
        {k.strip(): (v.strip() if v else "")
         for k, v in row.items() if k is not None}
        for row in reader
    ]


def _read_csv_path(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            {k.strip(): (v.strip() if v else "")
             for k, v in row.items() if k is not None}
            for row in reader
        ]


def _discover_templates() -> list[str]:
    return sorted(p.name for p in TEMPLATES_DIR.glob("*.html"))


def _output_path(row: dict, index: int) -> Path:
    raw  = row.get(FILENAME_COL) or f"report_{index:04d}"
    safe = sanitize_filename(raw)
    return OUTPUT_DIR / f"{safe}_{index:04d}.pdf"


def _make_zip(paths: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if p.exists():
                zf.write(p, p.name)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🏨 Report Generator")
    st.markdown("Bulk PDF reports from CSV data.")
    st.divider()

    templates = _discover_templates()

    st.markdown("### Templates")
    if templates:
        for t in templates:
            st.markdown(f"&nbsp;&nbsp;`{t}`", unsafe_allow_html=True)
    else:
        st.warning("No templates found in `templates/`")

    st.divider()
    st.markdown("### Output")
    st.markdown(f"`{OUTPUT_DIR.relative_to(PROJECT_DIR)}/`")

    st.divider()
    if st.button("🗑️ Clear previous results", use_container_width=True):
        for key in ("results", "elapsed"):
            st.session_state.pop(key, None)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("# Hotel AI Report Generator")
st.markdown(
    "Upload a CSV, preview your data, and generate one PDF per row "
    "using the templates in `templates/`."
)
st.divider()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1 — Data source
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-label">① Data Source</div>', unsafe_allow_html=True)

source_tab, preview_tab = st.tabs(["📂 Load CSV", "🔍 Preview Data"])

rows: list[dict] = []

with source_tab:
    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.markdown("**Use existing file**")
        if DEFAULT_CSV.exists():
            if st.button("Load `data.csv`", use_container_width=True):
                st.session_state["rows"] = _read_csv_path(DEFAULT_CSV)
                st.session_state["csv_name"] = "data.csv"
                st.success(f"Loaded {len(st.session_state['rows'])} rows from data.csv")
        else:
            st.warning("`data.csv` not found in project directory.")

    with col_right:
        st.markdown("**Upload your own CSV**")
        uploaded = st.file_uploader("", type=["csv"], label_visibility="collapsed")
        if uploaded:
            st.session_state["rows"] = _read_csv_upload(uploaded)
            st.session_state["csv_name"] = uploaded.name
            st.success(f"Loaded {len(st.session_state['rows'])} rows from {uploaded.name}")

rows = st.session_state.get("rows", [])

with preview_tab:
    if rows:
        csv_name = st.session_state.get("csv_name", "CSV")
        st.caption(f"{len(rows)} rows · {len(rows[0])} columns · {csv_name}")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=280)
    else:
        st.info("Load a CSV first to preview it here.")


st.divider()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2 — Generate
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="section-label">② Generate Reports</div>', unsafe_allow_html=True)

if not rows:
    st.warning("Load CSV data above before generating.")
elif not templates:
    st.error("No templates found. Add `.html` files to `templates/`.")
else:
    ready_col, info_col = st.columns([2, 1])

    with info_col:
        st.markdown(
            f"""
            | | |
            |---|---|
            | **Rows** | {len(rows)} |
            | **Templates** | {len(templates)} pages |
            | **PDFs to create** | {len(rows)} |
            """
        )

    with ready_col:
        generate_clicked = st.button(
            f"🚀 Generate {len(rows)} PDF Report{'s' if len(rows) != 1 else ''}",
            type="primary",
            use_container_width=True,
        )

    if generate_clicked:
        # Clear stale results from a previous run
        st.session_state.pop("results", None)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        progress_bar = st.progress(0.0, text="Starting…")
        status_msg   = st.empty()

        results  = []
        total    = len(rows)
        start_ts = time.perf_counter()

        for i, row in enumerate(rows, start=1):
            hotel = row.get(FILENAME_COL, f"Row {i}")
            out   = _output_path(row, i)

            progress_bar.progress(
                i / total,
                text=f"Generating {i} / {total} — {hotel}",
            )

            try:
                html = render_template(str(TEMPLATES_DIR), templates, row)
                generate_pdf(html, str(out))
                results.append({
                    "name": hotel, "file": out.name,
                    "path": out, "ok": True, "error": None,
                })
            except Exception as exc:
                results.append({
                    "name": hotel, "file": out.name,
                    "path": out, "ok": False, "error": str(exc),
                })

        progress_bar.empty()
        status_msg.empty()

        st.session_state["results"] = results
        st.session_state["elapsed"] = time.perf_counter() - start_ts
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3 — Results
# ═══════════════════════════════════════════════════════════════════════════════

results: list[dict] = st.session_state.get("results", [])
elapsed: float      = st.session_state.get("elapsed", 0.0)

if results:
    st.divider()
    st.markdown('<div class="section-label">③ Results</div>', unsafe_allow_html=True)

    ok_list   = [r for r in results if r["ok"]]
    fail_list = [r for r in results if not r["ok"]]

    # ── Stat cards ──────────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="stat-row">
          <div class="stat-card">
            <div class="label">Generated</div>
            <div class="value green">{len(ok_list)}</div>
          </div>
          <div class="stat-card">
            <div class="label">Failed</div>
            <div class="value {"red" if fail_list else "green"}">{len(fail_list)}</div>
          </div>
          <div class="stat-card">
            <div class="label">Total Time</div>
            <div class="value blue">{elapsed:.1f}s</div>
          </div>
          <div class="stat-card">
            <div class="label">Avg / Report</div>
            <div class="value">{elapsed/len(results):.2f}s</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Bulk download ────────────────────────────────────────────────────────
    if ok_list:
        zip_bytes = _make_zip([r["path"] for r in ok_list])
        st.download_button(
            label=f"⬇️ Download all {len(ok_list)} PDFs as ZIP",
            data=zip_bytes,
            file_name="hotel_ai_reports.zip",
            mime="application/zip",
            use_container_width=True,
            type="primary",
        )

    st.markdown("")

    # ── Per-file rows ────────────────────────────────────────────────────────
    st.markdown("**Individual files**")

    for r in results:
        cols = st.columns([0.45, 3.5, 1.2])

        cols[0].markdown(
            f'<span class="badge {"ok" if r["ok"] else "err"}">'
            f'{"OK" if r["ok"] else "ERR"}</span>',
            unsafe_allow_html=True,
        )
        cols[1].markdown(
            f'<span style="font-family:monospace;font-size:13px;">'
            f'{r["file"]}</span>',
            unsafe_allow_html=True,
        )

        if r["ok"] and r["path"].exists():
            cols[2].download_button(
                "Download PDF",
                data=r["path"].read_bytes(),
                file_name=r["file"],
                mime="application/pdf",
                key=f"dl_{r['file']}",
            )
        elif not r["ok"]:
            cols[2].markdown(
                f'<span style="font-size:11px;color:#dc2626;">'
                f'{(r["error"] or "")[:60]}</span>',
                unsafe_allow_html=True,
            )
