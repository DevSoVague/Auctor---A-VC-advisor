"""programs.xlsx: the competitor program table and the pursuit menu, for the Office workflow.

Owner: person 5. Every row carries its hypothesis id and claim ids, so it joins back to the memo.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from frontier.memo.candidates import candidates
from frontier.memo.charts import landscape_rows
from frontier.memo.models import MemoInput
from frontier.memo.scoring import scorecards

HEAD = PatternFill("solid", fgColor="F0EFEC")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEAD
    for row in rows:
        ws.append(row)
    for i, name in enumerate(header, 1):
        width = max([len(str(name))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 60)
    ws.freeze_panes = "A2"


def export(inp: MemoInput, path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet(
        wb,
        "Programs",
        [
            "Hypothesis",
            "Program",
            "Owner",
            "Owner type",
            "Mechanism",
            "Modality",
            "Stage",
            "Lead trial",
            "Fit to profile",
            "Deprioritized",
            "Flag reason",
            "Funding signal",
            "Novel edge",
            "Claim ids",
        ],
        [
            [
                r["hypothesis_id"],
                p.asset,
                p.owner,
                p.owner_type,
                p.mechanism,
                p.modality,
                p.stage,
                p.lead_trial,
                p.profile_fit,
                p.deprioritized,
                p.flag_reason,
                p.funding_signal,
                p.novel_edge,
                ", ".join(c.id for c in p.claims),
            ]
            for r in landscape_rows(inp)
            for p in [r["program"]]
        ],
    )
    _sheet(
        wb,
        "Pursuit options",
        ["Hypothesis", "Form", "Name", "Stage", "Fit", "Why listed", "Claim ids"],
        [
            [c.hypothesis_id, c.form, c.name, c.stage, c.fit, c.why_listed, ", ".join(c.claim_ids)]
            for menu in candidates(inp).values()
            for c in menu
        ],
    )
    _sheet(
        wb,
        "Scorecards",
        [
            "Hypothesis",
            "Mechanism",
            "Tier",
            "Level",
            "Crowding",
            "Best fit",
            "Claims checked",
            "Claims supported",
            "Upside",
            "Risk",
            "Return vs risk",
        ],
        [
            [
                s.hypothesis_id,
                s.mechanism,
                s.tier,
                s.level,
                s.crowding,
                s.best_fit,
                s.claims_checked,
                s.claims_supported,
                s.upside,
                s.risk,
                s.return_vs_risk,
            ]
            for s in scorecards(inp)
        ],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
