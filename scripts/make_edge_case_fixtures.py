"""Build the B7 edge-case documents.

Small ones are committed in corpus/fixtures/edge_cases/ so they can be uploaded by hand in the demo video:
  empty.pdf, scanned.pdf (an image of text, no text layer), contradict_2023.pdf + contradict_2024.docx (they disagree),
  table_prices.docx, special_chars.md, french_horaires.txt, urdu_notice.docx.
Large ones are NOT committed; `--large` writes them to data/edge_cases_large/ for manual demos:
  too_big_12mb.pdf (over the upload limit), too_many_pages_250.pdf (over the page limit), long_but_ok_120.pdf (accepted).

Run: python scripts/make_edge_case_fixtures.py [--large]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.make_files import make_pdf  # noqa: E402

OUT = ROOT / "corpus" / "fixtures" / "edge_cases"
LARGE = ROOT / "data" / "edge_cases_large"


def empty_pdf() -> bytes:
    return make_pdf([""])  # a valid page with no text at all


def scanned_pdf() -> bytes:
    """A page that is only a picture of text: what a scanner produces. There is no text layer to extract."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(["SCANNED SERVICE CONTRACT", "The warranty period is 24 months.", "Signed on 3 March 2026 by both parties."]):
        draw.text((40, 60 + i * 70), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=100)
    return buf.getvalue()


def contradict_2023() -> bytes:
    return make_pdf(["Refund policy, edition 2023. Customers may return any item within 30 days of purchase for a full refund."])


def contradict_2024() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("Refund policy, edition 2024", 1)
    d.add_paragraph("Customers may return an item within 14 days of purchase for a full refund. Later returns are not accepted.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def table_docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("Price list", 1)
    d.add_paragraph("Current prices and availability for the spring catalogue.")
    t = d.add_table(rows=4, cols=3)
    rows = [("Product", "Price", "Availability"), ("Widget A", "$12.50", "in stock"), ("Widget B", "$99.00", "backordered until May"),
            ("Widget C", "$7.25", "in stock")]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            t.cell(r, c).text = value
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


SPECIAL_MD = (
    "# Café notes\n\n"
    "The café’s Wi‑Fi password is “Zürich-Œuvre-42” (naïve cooks love it) — ﬁnal answer, no spaces.\n\n"
    "Opening hours: 07:00–19:00 \U0001F600 daily. Prices in € and ¥: 3€ ≤ 5¥ ∑ totals.\n\n"
    "Zero​width and right-to-left marks ‏‎ are present in this line on purpose.\n"
).encode("utf-8")

FRENCH_TXT = ("La bibliothèque municipale est ouverte du lundi au samedi, de neuf heures à dix-huit heures. "
              "Le prêt des livres est gratuit pour tous les résidents de la ville. La salle de lecture ferme le dimanche.\n").encode("utf-8")


def urdu_docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("دفتر کے اوقات", 1)  # office hours
    d.add_paragraph("دفتر پیر سے جمعہ تک صبح نو بجے سے شام پانچ بجے تک کھلا رہتا ہے۔")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


SMALL = {"empty.pdf": empty_pdf, "scanned.pdf": scanned_pdf, "contradict_2023.pdf": contradict_2023,
         "contradict_2024.docx": contradict_2024, "table_prices.docx": table_docx,
         "special_chars.md": lambda: SPECIAL_MD, "french_horaires.txt": lambda: FRENCH_TXT, "urdu_notice.docx": urdu_docx}


def long_pdf(pages: int) -> bytes:
    """A manual with one distinctive fact on page 100 (when there are that many pages) and filler everywhere else."""
    texts = []
    for i in range(1, pages + 1):
        if i == 100:
            texts.append("Page 100 of the facility manual. The emergency generator is located in building seven next to the loading dock.")
        else:
            texts.append(f"Page {i} of the facility manual. Maintenance log entry {i}: routine inspection completed, no faults were recorded today.")
    return make_pdf(texts)


def too_big_pdf() -> bytes:
    return b"%PDF-1.4\n" + b"% padding to exceed the upload limit\n" * (12 * 1024 * 1024 // 37 + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--large", action="store_true", help="also write the large files to data/edge_cases_large/ (not committed)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in SMALL.items():
        data = build()
        (OUT / name).write_bytes(data)
        print(f"{name:24} {len(data):>9,} bytes")
    if args.large:
        LARGE.mkdir(parents=True, exist_ok=True)
        for name, data in (("too_big_12mb.pdf", too_big_pdf()), ("too_many_pages_250.pdf", long_pdf(250)), ("long_but_ok_120.pdf", long_pdf(120))):
            (LARGE / name).write_bytes(data)
            print(f"{name:24} {len(data):>9,} bytes  -> data/edge_cases_large/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
