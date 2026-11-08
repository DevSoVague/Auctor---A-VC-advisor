"""Outline -> memo.docx with python-docx (owner: person 5).

Investor style: one serif-free sans face, a short title, bold lead-ins, bullets over paragraphs,
light-gray table headers, a shaded caution banner, and figures at page width.
"""

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from frontier.memo.sections import (
    Bullets,
    Callout,
    Figure,
    Heading,
    Outline,
    PageBreak,
    Para,
    Run,
    Table,
)

INK = RGBColor(0x0B, 0x0B, 0x0B)
INK_2 = RGBColor(0x52, 0x51, 0x4E)
ACCENT = RGBColor(0x2A, 0x78, 0xD6)
CRITICAL = RGBColor(0xD0, 0x3B, 0x3B)
FONT = "Calibri"


def _shade(cell_or_par, hex_fill: str) -> None:
    props = (
        cell_or_par._tc.get_or_add_tcPr()
        if hasattr(cell_or_par, "_tc")
        else cell_or_par._p.get_or_add_pPr()
    )
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    props.append(shd)


def _runs(par, runs: list[Run], size: float | None = None) -> None:
    for style, text in runs:
        r = par.add_run(text)
        r.bold = style == "b"
        r.italic = style == "i"
        if style == "i":
            r.font.color.rgb = INK_2
        if size:
            r.font.size = Pt(size)


def _setup(doc) -> None:
    base = doc.styles["Normal"]
    base.font.name = FONT
    base.font.size = Pt(10)
    base.font.color.rgb = INK
    base.paragraph_format.space_after = Pt(4)
    for name, size, color in (
        ("Heading 1", 16, INK),
        ("Heading 2", 13, ACCENT),
        ("Heading 3", 11, INK),
    ):
        st = doc.styles[name]
        st.font.name = FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(10)
        st.paragraph_format.space_after = Pt(3)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Inches(0.8)
        sec.top_margin = sec.bottom_margin = Inches(0.7)


def _table(doc, t: Table) -> None:
    table = doc.add_table(rows=1, cols=len(t.header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, text in zip(table.rows[0].cells, t.header, strict=True):
        cell.text = ""
        _runs(cell.paragraphs[0], [("b", text)], size=8.5)
        _shade(cell, "F0EFEC")
    # Repeat the header row, with its text, when a table runs onto the next page.
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(header)
    for row in t.rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, row, strict=False):
            cell.text = ""
            _runs(cell.paragraphs[0], [("", text)], size=8.5)
    if t.widths:
        table.autofit = False
        for row in table.rows:
            for cell, w in zip(row.cells, t.widths, strict=False):
                cell.width = Inches(w)
        for col, w in zip(table.columns, t.widths, strict=False):
            col.width = Inches(w)
    doc.add_paragraph()


def render(outline: Outline, path: Path) -> Path:
    doc = Document()
    _setup(doc)
    title = doc.add_paragraph()
    _runs(title, [("b", outline.title)], size=20)
    sub = doc.add_paragraph()
    _runs(sub, [("i", outline.subtitle)], size=9)

    for block in outline.blocks:
        if isinstance(block, Heading):
            doc.add_heading(block.text, level=block.level)
        elif isinstance(block, Para):
            _runs(doc.add_paragraph(), block.runs)
        elif isinstance(block, Bullets):
            for item in block.items:
                _runs(doc.add_paragraph(style="List Bullet"), item)
        elif isinstance(block, Table):
            _table(doc, block)
        elif isinstance(block, Figure):
            doc.add_picture(str(block.path), width=Inches(block.width_in))
            cap = doc.add_paragraph()
            _runs(cap, [("i", block.caption)], size=8.5)
        elif isinstance(block, Callout):
            head = doc.add_paragraph()
            _shade(head, "FBEAEA" if block.tone == "caution" else "F0EFEC")
            r = head.add_run(("⚠ " if block.tone == "caution" else "") + block.title.upper())
            r.bold = True
            r.font.color.rgb = CRITICAL if block.tone == "caution" else INK
            for line in block.lines:
                p = doc.add_paragraph(style="List Bullet")
                _shade(p, "FBEAEA" if block.tone == "caution" else "F0EFEC")
                _runs(p, [("", line)])
        elif isinstance(block, PageBreak):
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path
