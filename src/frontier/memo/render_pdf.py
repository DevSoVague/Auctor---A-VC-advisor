"""memo.docx -> memo.pdf with LibreOffice in headless mode (owner: person 5).

Optional: if LibreOffice isn't installed, returns None and the run keeps its Word file.
Install it with `brew install --cask libreoffice` (macOS) or `apt install libreoffice-writer`.
"""

import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

CANDIDATES = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")


def find_soffice() -> str | None:
    for name in CANDIDATES:
        found = shutil.which(name) or (name if Path(name).is_file() else None)
        if found:
            return found
    return None


def to_pdf(docx: Path, timeout: int = 120) -> Path | None:
    soffice = find_soffice()
    if soffice is None:
        warnings.warn(
            "LibreOffice not found; skipping memo.pdf (memo.docx is still written)", stacklevel=2
        )
        return None
    # A private profile dir lets this run alongside an open LibreOffice window.
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(docx.parent),
                str(docx),
            ],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    pdf = docx.with_suffix(".pdf")
    return pdf if pdf.exists() else None
