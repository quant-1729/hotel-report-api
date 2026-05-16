"""
Utility functions for the PDF report generation system.
"""

import csv
import logging
import re
from pathlib import Path
from jinja2 import ChainableUndefined, Environment, FileSystemLoader, TemplateNotFound

# Library module — never call basicConfig here; let the entry point own it.
logger = logging.getLogger(__name__)


def load_csv(filepath: str) -> list[dict]:
    """Load all rows from a CSV file into a list of dicts."""
    rows = []
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Strip whitespace; drop None keys produced by trailing commas.
            clean = {
                k.strip(): (v.strip() if v else "")
                for k, v in row.items()
                if k is not None
            }
            # Skip fully-empty rows (trailing newlines in CSVs)
            if any(clean.values()):
                rows.append(clean)

    logger.info(f"Loaded {len(rows)} rows from {filepath}")
    return rows


def render_template(
    template_dir: str,
    template_names: list[str],
    context: dict,
    fallback: str = "N/A",
) -> str:
    """
    Render one or more Jinja2 templates into a single valid HTML document.

    Each template is a standalone HTML file (so it previews correctly on its
    own). When merging, we:
      1. Transform the raw CSV row into a structured context the templates
         expect (competitors list, executive stats, revenue metrics, …).
      2. Render every template with that context.
      3. Extract the <style> block(s) and <body> content from each.
      4. Deduplicate the @page rule — the first one wins.
      5. Wrap each page's body in a <div class="report-page"> and add
         `page-break-after: always` on every page except the last.
      6. Return one well-formed HTML document that Chromium can render
         correctly with A4 sizing and clean page breaks.

    Missing context keys fall back to `fallback` via the _FallbackUndefined
    class (rendered as `fallback`, falsy in boolean context, chainable for
    attribute / item lookups — so `{{ foo.bar.baz }}` never crashes).
    """
    # Build a _FallbackUndefined class closed over this call's fallback token
    # so callers can override "N/A" per-call without a module-level mutation.
    class _CallFallbackUndefined(ChainableUndefined):
        __slots__ = ()

        def __str__(self) -> str:
            return fallback

        def __html__(self) -> str:
            return fallback

        def __bool__(self) -> bool:
            return False

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,
        undefined=_CallFallbackUndefined,
    )

    structured_context = build_context(context)

    style_blocks: list[str] = []
    body_fragments: list[str] = []

    for name in template_names:
        try:
            tmpl = env.get_template(name)
            rendered = tmpl.render(structured_context)
            style_blocks.append(_extract_styles(rendered))
            body_fragments.append(_extract_body(rendered))
        except TemplateNotFound:
            logger.error(f"Template not found: {name} — skipping")
        except Exception as exc:
            logger.error(f"Error rendering {name}: {exc} — skipping")

    if not body_fragments:
        return ""

    merged_css = _merge_styles(style_blocks)

    # Wrap each page; only non-last pages get a forced break after them.
    last = len(body_fragments) - 1
    page_divs = []
    for i, fragment in enumerate(body_fragments):
        break_style = ' style="page-break-after: always;"' if i < last else ""
        page_divs.append(f'<div class="report-page"{break_style}>\n{fragment}\n</div>')

    return _build_document(merged_css, "\n".join(page_divs))


# ---------------------------------------------------------------------------
# Schema bridge: flat CSV row  ->  structured template context
# ---------------------------------------------------------------------------

def build_context(row: dict) -> dict:
    """
    Transform a flat CSV row (ai_visibility_report_template schema) into the
    structured context the page3-7 templates expect.

    The transform:
      • Preserves every original column (pass-through for anything the
        templates reference directly, e.g. `hotel_name`, `city`).
      • Derives scalar fields the templates read individually
        (`ai_visibility_score`, `top_competitor_score`, `discovery_score`, …)
        with severity-driven colour classes.
      • Builds list-shaped fields the templates iterate over
        (`competitors`, `revenue_metrics`, `blur_summary_items`).
      • Enables the page-5 preview blur by default (`preview_mask_enabled`).

    If a row already provides a key, it is NOT overwritten (setdefault
    semantics for top-level keys except the derived score/competitor lists
    which are always rebuilt from the flat CSV columns).
    """
    ctx: dict = dict(row) if isinstance(row, dict) else {}

    def _num(key: str, default: int = 0) -> int:
        raw = ctx.get(key, "")
        if raw in (None, ""):
            return default
        try:
            return int(float(str(raw).replace(",", "").strip().rstrip("%")))
        except (ValueError, TypeError):
            return default

    import random as _random
    visibility    = _num("visibility_score", 0)
    total_score   = 100
    mentioned     = _num("mentioned_prompts", 0)
    total_prompts = 20
    top_comp      = _num("competitor_score", 0)
    discovery     = _num("discovery_score", 0)

    # Ensure visibility is never 0; add a small random bump (1–2 pts) per hotel.
    _seed = hash(ctx.get("hotel_name", "") or "")
    _rng  = _random.Random(_seed)
    visibility = max(1, visibility) + _rng.randint(1, 2)

    # ── Page 3 · Cover ────────────────────────────────────────────────────
    ctx.setdefault("report_period", ctx.get("report_date") or "April 2026")
    ctx.setdefault("market_name",   ctx.get("city") or "")

    # ── Page 4 · Executive Summary ────────────────────────────────────────
    ctx["ai_visibility_score"] = visibility
    ctx["ai_visibility_denominator"] = f"/ {total_score}"
    ctx["prompts_mentioned_count"] = mentioned
    ctx["prompts_mentioned_denominator"] = f"/ {total_prompts}"
    pct = int(round(100 * mentioned / total_prompts)) if total_prompts else 0
    ctx["prompts_mentioned_percentage"] = pct
    ctx["prompts_progress_label"] = (
        f"{pct}% mention rate across {total_prompts} tested prompts"
    )
    ctx["top_competitor_score"] = top_comp
    ctx["discovery_score"] = discovery
    if ctx.get("summary_findings"):
        ctx["findings_summary"] = ctx["summary_findings"]
    if ctx.get("optimize_discovery_signals"):
        ctx["recommendation_text"] = ctx["optimize_discovery_signals"]

    # Severity classes: visibility
    cls, col, status = _severity(visibility)
    ctx.setdefault("score_class", cls)
    ctx.setdefault("score_accent_color", col)
    ctx.setdefault("score_value_color", col)
    ctx.setdefault("score_status_color", col)
    ctx.setdefault("score_status_dot_color", col)
    ctx.setdefault("score_status_text", status)

    # Severity classes: discovery
    dcls, dcol, dstatus = _severity(discovery)
    ctx.setdefault("discovery_card_class", dcls)
    ctx.setdefault("discovery_accent_color", dcol)
    ctx.setdefault("discovery_status_color", dcol)
    ctx.setdefault("discovery_status_dot_color", dcol)
    ctx.setdefault(
        "discovery_status_text",
        "Zero Mentions in Category Discovery" if discovery < 45 else dstatus,
    )

    # ── Page 5 · Audit + Blur ─────────────────────────────────────────────
    # The mask is intentionally ON by default — this is a preview report.
    ctx.setdefault("preview_mask_enabled", True)

    missing = _num("missing_queries_count", 0)
    if missing > 0 and "teaser_rows_label" not in ctx:
        ctx["teaser_rows_label"] = (
            f"+ {missing} more prompts hidden — unlock the full audit"
        )

    ctx.setdefault("aggregated_visibility_score", f"{visibility} / {total_score}")

    top_comp_name = ctx.get("competitor_1_name") or "Market leader"
    key_insight   = ctx.get("key_insight") or (
        "Visibility drops sharply outside luxury intents, while competitors "
        "capture family and budget queries."
    )
    ctx.setdefault("blur_summary_items", [
        {"label": "Key Insight", "text": key_insight},
        {"label": "Top Competitor",
         "text": f"{top_comp_name} leads with {top_comp}/100 across tested prompts."},
        {"label": "Critical Action",
         "text": ctx.get("optimize_discovery_signals")
                 or "Optimize structured data for high-intent queries."},
    ])

    # ── Page 6 · Competitor Analysis ──────────────────────────────────────
    ctx["competitors"] = _build_competitors(ctx, visibility)
    ctx["revenue_metrics"] = _build_revenue_metrics(ctx)

    # ── Page 7 · Pricing / CTA ────────────────────────────────────────────
    if ctx.get("price"):
        ctx.setdefault("pricing_amount", ctx["price"])
    if ctx.get("cta_link"):
        ctx.setdefault("pricing_cta_url", ctx["cta_link"])

    return ctx


def _severity(score: int) -> tuple[str, str, str]:
    """Return (css-class, hex-color, status-label) for a 0-100 score."""
    if score >= 70:
        return "success", "#10b981", "Strong Performance"
    if score >= 45:
        return "warning", "#f59e0b", "Needs Improvement"
    return "danger", "#ef4444", "Critical Underperformance"


_COMP_PALETTE = [
    ("#3525cd", "#3525cd"),
    ("#4f46e5", "#4f46e5"),
    ("#c0c7d6", "#585f6c"),
]


def _build_competitors(ctx: dict, visibility: int) -> list[dict]:
    """Convert competitor_1..3 flat columns into the scoreboard list."""
    rows: list[dict] = []
    for idx in range(1, 4):
        name = ctx.get(f"competitor_{idx}_name")
        if not name:
            continue
        try:
            score = int(float(str(ctx.get(f"competitor_{idx}_score") or 0)))
        except (ValueError, TypeError):
            score = 0
        fill, scol = _COMP_PALETTE[idx - 1]
        rows.append({
            "rank": f"{idx:02d}",
            "name": name,
            "segment": ctx.get(f"competitor_{idx}_category") or "",
            "score": score,
            "score_label": f"Scores {score}/100 in market",
            "fill_color": fill,
            "score_color": scol,
            "width": score,
            "row_background": "#ffffff",
            "badge_background": "#e7e8e9",
            "rank_color": "#edeeef",
            "property_name_color": "#191c1d",
        })

    # Subject property always appears last, highlighted in red.
    subject_label = (
        "Invisible for discovery" if visibility < 45
        else "Needs uplift" if visibility < 70
        else "Competitive"
    )
    rows.append({
        "rank": f"{len(rows) + 1:02d}",
        "name": ctx.get("hotel_name") or "Subject Property",
        "segment": "Subject Property",
        "score": visibility,
        "score_label": subject_label,
        "fill_color": "#ba1a1a",
        "score_color": "#ba1a1a",
        "width": visibility,
        "row_background": "rgba(255,218,214,0.2)",
        "badge_background": "#ffdad6",
        "rank_color": "#ffdad6",
        "property_name_color": "#ba1a1a",
    })
    return rows


def _build_revenue_metrics(ctx: dict) -> list[dict]:
    extra_points  = ctx.get("extra_points")  or "+0 pts"
    extra_prompts = ctx.get("extra_prompts") or ""
    extra_guests  = ctx.get("extra_guests")  or "~0 Guests"
    avg_night     = ctx.get("average_night") or ""
    monthly_loss  = ctx.get("monthly_loss")  or ""
    annual_loss   = ctx.get("annual_loss")   or ""

    return [
        {
            "label": "Market Leader Advantage",
            "value": str(extra_points),
            "body":  "The top competitor scores meaningfully higher across general-intent prompts.",
            "footnote": (
                f"That's {extra_prompts} more prompts where they are the #1 recommendation."
                if extra_prompts else
                "More prompts currently surface them above you."
            ),
            "accent_color": "#3525cd",
        },
        {
            "label": "Guest Differential",
            "value": str(extra_guests),
            "body":  "Properties scoring 70+ receive materially more direct booking intent clicks.",
            "footnote": (
                f"At your average nightly rate of {avg_night}, that gap compounds quickly."
                if avg_night else
                "That gap compounds quickly."
            ),
            "accent_color": "#3525cd",
        },
        {
            "label": "Annual Revenue at Stake",
            "value": str(annual_loss) if annual_loss else "—",
            "body":  (
                f"Monthly loss trending at {monthly_loss}."
                if monthly_loss else
                "A conservative estimate from current AI search volumes."
            ),
            "footnote": "This gap widens as AI adoption grows.",
            "accent_color": "#3525cd",
        },
    ]


# ---------------------------------------------------------------------------
# HTML extraction helpers
# ---------------------------------------------------------------------------

_STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
_BODY_RE  = re.compile(r"<body[^>]*>(.*?)</body>",  re.DOTALL | re.IGNORECASE)
_PAGE_RULE_RE = re.compile(r"@page\s*\{[^}]*\}", re.DOTALL)


def _extract_styles(html: str) -> str:
    """Return the concatenated text of all <style> blocks in an HTML string."""
    return "\n".join(m.group(1) for m in _STYLE_RE.finditer(html))


def _extract_body(html: str) -> str:
    """Return the innerHTML of the <body> tag, or the full string as fallback."""
    m = _BODY_RE.search(html)
    return m.group(1).strip() if m else html.strip()


def _merge_styles(blocks: list[str]) -> str:
    """
    Concatenate CSS blocks from all templates, keeping only the first
    @page rule encountered so Chromium gets an unambiguous page size.
    """
    merged: list[str] = []
    page_rule_emitted = False

    for block in blocks:
        if not block.strip():
            continue
        if not page_rule_emitted and _PAGE_RULE_RE.search(block):
            merged.append(block)
            page_rule_emitted = True
        else:
            clean = _PAGE_RULE_RE.sub("", block).strip()
            if clean:
                merged.append(clean)

    return "\n\n".join(merged)


def _build_document(css: str, body_content: str) -> str:
    """Wrap merged CSS and page content into a single valid HTML document."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="UTF-8" />\n'
        "  <style>\n"
        f"{css}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"{body_content}\n"
        "</body>\n"
        "</html>"
    )


# ---------------------------------------------------------------------------
# Filename & PDF helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str, max_length: int = 80) -> str:
    """
    Convert an arbitrary string into a safe filename.
    Replaces non-alphanumeric characters with underscores and trims length.
    """
    name = re.sub(r"[^\w\-.]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:max_length] or "report"


def generate_pdf(html: str, output_path: str) -> None:
    """
    Render an HTML string to a PDF file using Playwright (Chromium).

    Runs Playwright in a fresh subprocess via `_pdf_worker.py`. This sidesteps
    the Windows asyncio issue where Streamlit/Tornado sets
    WindowsSelectorEventLoopPolicy — an event loop that cannot spawn
    subprocesses on Windows, which Playwright requires to launch Chromium.
    """
    if not html.strip():
        raise ValueError("Rendered HTML is empty — all templates failed or were skipped")

    import subprocess   # noqa: PLC0415
    import sys          # noqa: PLC0415
    import tempfile     # noqa: PLC0415

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    worker = Path(__file__).parent / "_pdf_worker.py"
    if not worker.exists():
        raise FileNotFoundError(f"PDF worker script missing: {worker}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html)
        html_tmp = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, str(worker), html_tmp, str(output_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"PDF worker failed ({result.returncode}): {err[:300]}")
    finally:
        try:
            Path(html_tmp).unlink()
        except OSError:
            pass
