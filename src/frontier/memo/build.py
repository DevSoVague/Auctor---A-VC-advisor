"""One call from a finished run to the memo files (owner: person 5).

    write_memo(inp, folder) -> {"docx": ..., "pdf": ... or None, "xlsx": ..., "charts": {...}}

Command line, from a saved run:
    uv run python -m frontier.memo.build runs/<id>/result.json [out_folder]
"""

import sys
from pathlib import Path

from frontier.memo import charts, export_xlsx, render_docx, render_pdf
from frontier.memo.draft import MemoDraft, draft_from_numbers
from frontier.memo.inputs import from_result_json
from frontier.memo.models import MemoInput
from frontier.memo.sections import build_outline


def write_memo(
    inp: MemoInput, folder: Path, draft: MemoDraft | None = None, pdf: bool = True
) -> dict:
    """Render memo.docx (always), memo.pdf (if LibreOffice is installed) and programs.xlsx."""
    folder = Path(folder)
    draft = draft or draft_from_numbers(inp)
    pngs = charts.all_charts(inp, draft, folder / "charts")
    docx = render_docx.render(build_outline(inp, draft, pngs), folder / "memo.docx")
    return {
        "draft": draft,
        "docx": docx,
        "pdf": render_pdf.to_pdf(docx) if pdf else None,
        "xlsx": export_xlsx.export(inp, folder / "programs.xlsx"),
        "charts": pngs,
    }


if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.parent
    files = write_memo(from_result_json(src), out)
    for key in ("docx", "pdf", "xlsx"):
        print(f"{key}: {files[key]}")
