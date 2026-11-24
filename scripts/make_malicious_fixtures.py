"""Build files that must be REJECTED on upload (B6) into corpus/fixtures/malicious/, for manual testing and the demo video.

None of them does anything harmful: they only carry the markers a scanner looks for (a JavaScript or launch name in a PDF,
a macro part in a DOCX, a highly compressed archive, an encrypted PDF, a broken PDF).

Run: python scripts/make_malicious_fixtures.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.make_files import make_macro_docx, make_pdf, make_zip_bomb_docx  # noqa: E402

OUT = ROOT / "corpus" / "fixtures" / "malicious"
TEXT = ["A harmless looking page of ordinary text that is long enough to count as a real document."]


def encrypted_pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf(TEXT))).pages:
        writer.add_page(page)
    writer.encrypt("secret-password")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


FILES = {
    "javascript.pdf": ("400 malicious_file", make_pdf(TEXT).replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >>")),
    "launch_action.pdf": ("400 malicious_file", make_pdf(TEXT).replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /Launch /F (cmd.exe) >>")),
    "macro.docx": ("400 malicious_file", make_macro_docx()),
    "zip_bomb.docx": ("400 malicious_file", make_zip_bomb_docx()),
    "encrypted.pdf": ("400 encrypted_file", encrypted_pdf()),
    "broken.pdf": ("400 corrupt_file", b"%PDF-1.4\nthis is not a real pdf body at all, just text pretending to be one"),
    "fake.pdf": ("415 unsupported_type", b"This is plain text with a .pdf name, not a PDF."),
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (expected, data) in FILES.items():
        (OUT / name).write_bytes(data)
        print(f"{name:20} expected upload result: {expected}   ({len(data):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
