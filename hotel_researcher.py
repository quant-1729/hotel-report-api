"""
Hotel research module: DuckDuckGo search + Gemini Flash scoring.

Given a hotel name (and optional city), this module:
  1. Runs multiple DuckDuckGo searches to gather web presence data
  2. Sends the aggregated snippets to Gemini 2.5 Flash Lite
  3. Returns a structured dict matching the data.csv schema
"""

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from ddgs import DDGS
from google import genai

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Models to try in order (first that works wins)
GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

MAX_RETRIES = 2
RETRY_DELAY_SECS = 10

SEARCH_QUERIES = [
    '"{hotel}" hotel reviews ratings',
    '"{hotel}" hotel amenities location',
    '"{hotel}" TripAdvisor booking.com',
    '"{hotel}" AI travel recommendation',
    '"{hotel}" competitors nearby hotels',
]

GEMINI_PROMPT = """You are a hotel AI visibility analyst. Based on the web search results below,
generate a structured scoring report for this hotel.

HOTEL NAME: {hotel_name}
CITY: {city}

WEB SEARCH RESULTS:
{search_results}

Respond with ONLY valid JSON (no markdown fences, no commentary) matching this exact schema:

{{
  "hotel_name": "{hotel_name}",
  "city": "{city}",
  "report_date": "May 2026",
  "visibility_score": <int 0-100, how visible is this hotel in AI/search results>,
  "total_score": 100,
  "mentioned_prompts": <int 0-20, estimated prompts where this hotel would appear>,
  "total_prompts": 20,
  "competitor_score": <int 0-100, top competitor's estimated score>,
  "discovery_score": <int 0-100, how discoverable via category searches>,
  "summary_findings": "<2-3 sentence analysis of the hotel's AI visibility strengths and weaknesses>",
  "optimize_discovery_signals": "<1-2 sentence recommendation for improving AI discoverability>",
  "missing_queries_count": <int, estimated prompts where hotel is NOT mentioned>,
  "competitor_1_name": "<name of strongest local competitor>",
  "competitor_1_category": "<competitor's positioning, e.g. 'Luxury Leader'>",
  "competitor_1_score": <int 0-100>,
  "competitor_2_name": "<second competitor>",
  "competitor_2_category": "<positioning>",
  "competitor_2_score": <int 0-100>,
  "competitor_3_name": "<third competitor>",
  "competitor_3_category": "<positioning>",
  "competitor_3_score": <int 0-100>,
  "extra_points": "+<int> pts",
  "extra_prompts": "<int>",
  "extra_guests": "~<int> Guests",
  "average_night": "<currency and amount if found, else empty string>",
  "monthly_loss": "<estimated monthly revenue loss, else empty string>",
  "annual_loss": "<estimated annual revenue loss, else empty string>",
  "price": "",
  "cta_link": "https://zerochills.com/book",
  "key_insight": "<1 sentence key insight about visibility gaps>"
}}

Rules:
- Base scores on ACTUAL web presence signals from the search results
- A hotel with strong TripAdvisor/Booking.com presence but weak AI mentions: visibility 30-50
- A hotel barely found in search results: visibility 10-25
- A well-known chain with many AI mentions: visibility 60-85
- competitor_score should always be higher than visibility_score (to show the gap)
- discovery_score is typically lower than visibility_score for lesser-known hotels
- Be realistic and data-driven, not generous
"""


def search_hotel(hotel_name: str, max_results_per_query: int = 5) -> str:
    """Run multiple DuckDuckGo searches and return aggregated snippets."""
    all_snippets: list[str] = []

    ddgs = DDGS()
    for query_template in SEARCH_QUERIES:
        query = query_template.format(hotel=hotel_name)
        try:
            results = ddgs.text(query, max_results=max_results_per_query)
            for r in results:
                title = r.get("title", "")
                body = r.get("body", "")
                href = r.get("href", "")
                all_snippets.append(f"[{title}] ({href})\n{body}")
        except Exception as e:
            logger.warning(f"Search failed for '{query}': {e}")
            continue

    if not all_snippets:
        return "(No search results found)"

    return "\n\n---\n\n".join(all_snippets)


def score_with_gemini(hotel_name: str, city: str, search_results: str) -> dict:
    """Send search results to Gemini and get structured scoring."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = GEMINI_PROMPT.format(
        hotel_name=hotel_name,
        city=city or "unknown",
        search_results=search_results[:15000],
    )

    # Try models in order with retries on rate limits
    last_error = None
    raw = None
    for attempt in range(MAX_RETRIES + 1):
        for model in GEMINI_MODELS:
            try:
                logger.info(f"Trying Gemini model: {model} (attempt {attempt + 1})")
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                raw = response.text.strip()
                break
            except Exception as e:
                last_error = e
                err_str = str(e)
                logger.warning(f"Model {model} failed: {err_str[:150]}")
                continue
        if raw:
            break
        if attempt < MAX_RETRIES:
            logger.info(f"All models failed on attempt {attempt + 1}, retrying in {RETRY_DELAY_SECS}s...")
            time.sleep(RETRY_DELAY_SECS)

    if not raw:
        raise ValueError(f"All Gemini models failed after {MAX_RETRIES + 1} attempts. Last error: {last_error}")

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    if raw.startswith("json"):
        raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Gemini returned invalid JSON: {e}\nRaw: {raw[:500]}")
        raise ValueError(f"AI returned invalid scoring data: {e}")

    # Ensure all expected keys exist with sensible defaults
    data.setdefault("hotel_name", hotel_name)
    data.setdefault("city", city)
    data.setdefault("report_date", "May 2026")
    data.setdefault("total_score", 100)
    data.setdefault("total_prompts", 20)
    data.setdefault("cta_link", "https://zerochills.com/book")
    data.setdefault("price", "")

    # Convert numeric values to strings (CSV schema expects strings)
    for key in data:
        if isinstance(data[key], (int, float)):
            data[key] = str(data[key])

    return data


def research_hotel(hotel_name: str, city: str = "") -> dict:
    """
    Full pipeline: search the web for hotel data, then score with Gemini.

    Returns a dict matching the data.csv schema, ready to be passed to
    the report template renderer.
    """
    logger.info(f"Researching hotel: {hotel_name} ({city or 'unknown city'})")

    search_results = search_hotel(hotel_name)
    snippet_count = search_results.count("---") + 1 if "---" in search_results else 0
    logger.info(f"Gathered {len(search_results)} chars of search data ({snippet_count} snippets)")

    data = score_with_gemini(hotel_name, city, search_results)
    logger.info(
        f"Gemini scored {hotel_name}: visibility={data.get('visibility_score')}, "
        f"discovery={data.get('discovery_score')}"
    )

    return data
