"""
Bulk PDF report generator.

Usage:
    python generate.py [--csv data.csv] [--templates templates] [--output output]

Each CSV row is rendered into a multi-page PDF using all HTML templates
found in the templates directory (sorted alphabetically).
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Configure logging once here — the entry point owns this, not library modules.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from utils import load_csv, render_template, generate_pdf, sanitize_filename  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
DEFAULT_CSV = "data.csv"
DEFAULT_TEMPLATES_DIR = "templates"
DEFAULT_OUTPUT_DIR = "output"

# Column used to build the output filename. Falls back to row index.
FILENAME_COLUMN = "hotel_name"


def discover_templates(templates_dir: str) -> list[str]:
    """Return template filenames sorted alphabetically."""
    path = Path(templates_dir)
    if not path.is_dir():
        raise NotADirectoryError(f"Templates directory not found: {templates_dir}")
    names = sorted(p.name for p in path.glob("*.html"))
    if not names:
        raise RuntimeError(f"No .html templates found in {templates_dir}")
    logger.info(f"Found {len(names)} template(s): {', '.join(names)}")
    return names


def build_output_path(output_dir: str, row: dict, index: int) -> str:
    """Derive a clean output PDF path from the row data."""
    raw_name = row.get(FILENAME_COLUMN) or f"report_{index:04d}"
    safe_name = sanitize_filename(raw_name)
    return str(Path(output_dir) / f"{safe_name}_{index:04d}.pdf")


def run(csv_path: str, templates_dir: str, output_dir: str) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    rows = load_csv(csv_path)
    templates = discover_templates(templates_dir)

    total = len(rows)
    success = 0
    failures = 0
    start = time.perf_counter()

    for i, row in enumerate(rows, start=1):
        output_path = build_output_path(output_dir, row, i)

        # Progress indicator
        print(f"\rGenerating {i}/{total} — {Path(output_path).name}   ", end="", flush=True)

        try:
            html = render_template(templates_dir, templates, row)
            generate_pdf(html, output_path)
            success += 1
        except Exception as exc:
            failures += 1
            print()  # end the \r line so the error appears on its own line
            logger.error(f"[Row {i}/{total}] {Path(output_path).name} — {exc}")

    elapsed = time.perf_counter() - start
    print()  # newline after progress indicator
    logger.info(
        f"Done. {success}/{total} PDFs generated in {elapsed:.1f}s"
        + (f" | {failures} error(s)" if failures else "")
    )
    logger.info(f"Output directory: {Path(output_dir).resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk HTML → PDF report generator")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to CSV data file")
    parser.add_argument("--templates", default=DEFAULT_TEMPLATES_DIR, help="Templates directory")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output directory for PDFs")
    args = parser.parse_args()

    try:
        run(args.csv, args.templates, args.output)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        logger.error(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
