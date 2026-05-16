"""
Subprocess worker for Playwright-based PDF rendering.

This runs as a fresh Python process so it gets a clean asyncio event loop —
avoiding the Tornado/Streamlit SelectorEventLoop conflict on Windows that
breaks Playwright's sync API.

Usage:
    python _pdf_worker.py <html_input_path> <pdf_output_path>

Reads HTML from the input file (UTF-8) and writes the rendered PDF to the
output path. Exits 0 on success, non-zero with stderr on failure.
"""

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: _pdf_worker.py <html_input_path> <pdf_output_path>", file=sys.stderr)
        return 2

    html_path = Path(sys.argv[1])
    pdf_path  = Path(sys.argv[2])

    if not html_path.exists():
        print(f"Input HTML not found: {html_path}", file=sys.stderr)
        return 2

    html = html_path.read_text(encoding="utf-8")

    from playwright.sync_api import sync_playwright

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
            )
        finally:
            browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
