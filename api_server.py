"""
FastAPI server for on-demand hotel report PDF generation.

Accepts a Google Maps URL, extracts the hotel name, and returns a PDF.
Fallback chain: CSV lookup → AI web research (DuckDuckGo + Gemini) → static defaults.

Run locally:
    cd project
    uvicorn api_server:app --reload --port 8000

Production (Railway/Render):
    uvicorn api_server:app --host 0.0.0.0 --port $PORT
"""

import csv
import logging
import os
import re
import tempfile
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from utils import generate_pdf, render_template, sanitize_filename

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Hotel AI Report Generator API")

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

PROJECT_DIR = Path(__file__).parent
TEMPLATES_DIR = PROJECT_DIR / "templates"
DATA_CSV = PROJECT_DIR / "data.csv"

DEFAULTS = {
    "city": "",
    "report_date": "May 2026",
    "visibility_score": "28",
    "total_score": "100",
    "mentioned_prompts": "3",
    "total_prompts": "20",
    "competitor_score": "72",
    "discovery_score": "18",
    "summary_findings": (
        "This property is currently invisible across the majority of AI-driven "
        "travel discovery queries. Urgent optimisation is needed to compete in "
        "the evolving search landscape."
    ),
    "optimize_discovery_signals": (
        "Implement structured data markup, optimise Google Business Profile, and "
        "build topical authority through content aligned with high-intent travel queries."
    ),
    "missing_queries_count": "14",
    "competitor_1_name": "Market Leader",
    "competitor_1_category": "Category Leader",
    "competitor_1_score": "72",
    "competitor_2_name": "Top Competitor",
    "competitor_2_category": "Established Brand",
    "competitor_2_score": "65",
    "competitor_3_name": "Rising Challenger",
    "competitor_3_category": "Emerging Player",
    "competitor_3_score": "58",
    "extra_points": "+44 pts",
    "extra_prompts": "14",
    "extra_guests": "~35 Guests",
    "average_night": "",
    "monthly_loss": "",
    "annual_loss": "",
    "price": "",
    "cta_link": "https://zerochills.com/book",
    "key_insight": (
        "This property has near-zero visibility in AI-powered travel discovery. "
        "Competitors dominate across all tested prompt categories."
    ),
}


class GenerateRequest(BaseModel):
    maps_url: str


def extract_hotel_name(maps_url: str) -> str:
    """Extract hotel/place name from a Google Maps URL."""
    decoded = urllib.parse.unquote(maps_url)

    # Pattern: /maps/place/Hotel+Name+Here/...
    match = re.search(r"/maps/place/([^/@]+)", decoded)
    if match:
        raw = match.group(1)
        name = raw.replace("+", " ").replace("_", " ")
        name = re.sub(r"\s+", " ", name).strip()
        return name

    # Pattern: search query param ?q=Hotel+Name
    parsed = urllib.parse.urlparse(decoded)
    params = urllib.parse.parse_qs(parsed.query)
    if "q" in params:
        return params["q"][0].strip()

    return ""


def lookup_csv(hotel_name: str) -> dict | None:
    """Look up a hotel row in data.csv by fuzzy name matching."""
    if not DATA_CSV.exists():
        return None

    normalized = hotel_name.lower().strip()

    with open(DATA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_name = (row.get("hotel_name") or "").strip()
            if csv_name.lower() == normalized:
                return {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

            # Partial match: if either contains the other
            if normalized in csv_name.lower() or csv_name.lower() in normalized:
                return {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

    return None


def build_row(hotel_name: str, maps_url: str) -> tuple[dict, str]:
    """
    Build a report data row using the 3-tier fallback chain:
      A) CSV lookup — exact/partial match in data.csv
      B) AI research — DuckDuckGo search + Gemini Flash scoring
      C) Static defaults — template defaults with hotel name

    Returns (row_dict, source) where source is "csv", "ai", or "defaults".
    """
    # ── Tier A: CSV lookup ──────────────────────────────────────────
    row = lookup_csv(hotel_name)
    if row:
        logger.info(f"[{hotel_name}] Found in data.csv")
        return row, "csv"

    # ── Tier B: AI web research ─────────────────────────────────────
    try:
        from hotel_researcher import research_hotel  # noqa: PLC0415
        ai_row = research_hotel(hotel_name)
        if ai_row and ai_row.get("hotel_name"):
            logger.info(f"[{hotel_name}] AI research successful (visibility={ai_row.get('visibility_score')})")
            return ai_row, "ai"
    except Exception as exc:
        logger.warning(f"[{hotel_name}] AI research failed, falling back to defaults: {exc}")

    # ── Tier C: Static defaults ─────────────────────────────────────
    logger.info(f"[{hotel_name}] Using static defaults")
    row = dict(DEFAULTS)
    row["hotel_name"] = hotel_name or "Your Hotel"
    return row, "defaults"


@app.post("/generate-report")
async def generate_report(req: GenerateRequest):
    hotel_name = extract_hotel_name(req.maps_url)
    if not hotel_name:
        raise HTTPException(
            status_code=400,
            detail="Could not extract hotel name from the provided URL. "
            "Please use a full Google Maps place URL.",
        )

    row, source = build_row(hotel_name, req.maps_url)

    templates = sorted(p.name for p in TEMPLATES_DIR.glob("*.html"))
    if not templates:
        raise HTTPException(status_code=500, detail="No templates found on server.")

    html = render_template(str(TEMPLATES_DIR), templates, row)
    if not html.strip():
        raise HTTPException(status_code=500, detail="Template rendering produced empty output.")

    safe_name = sanitize_filename(hotel_name or "report")
    filename = f"{safe_name}_AI_Visibility_Report.pdf"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        generate_pdf(html, tmp_path)
        pdf_bytes = Path(tmp_path).read_bytes()
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    logger.info(f"[{hotel_name}] PDF generated ({len(pdf_bytes)} bytes, source={source})")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Hotel-Name": hotel_name,
            "X-Data-Source": source,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "templates": len(list(TEMPLATES_DIR.glob("*.html")))}
