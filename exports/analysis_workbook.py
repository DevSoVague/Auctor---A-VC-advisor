"""Live backend workbook for person 3's agents: runs/<run_id>/analysis.xlsx.

Subscribes to the event hook and rewrites the workbook (atomically) every time A1/A2/A3/A6
finishes, a card is rejected/killed/revised, or a reviewer rerun lands. Every row carries
hypothesis_id and source_ids so person 4's programs.xlsx and person 2's ledger can join.

Usage:
    wb = AnalysisWorkbook(ctx, Path("runs") / ctx.run_id / "analysis.xlsx")
    wb.attach()          # now updates live
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
VERDICT_FILL = {"survives": "D9EAD3", "advance": "D9EAD3", "revise": "FFF2CC", "kill": "F4CCCC",
                "rejected": "EEEEEE"}

SHEETS = {
    "WhyNow": ["date", "type", "effect_on", "summary", "source_ids", "confidence"],
    "Segments": ["segment", "inclusion_criteria", "source_ids"],
    "Hypotheses": ["rank", "hypothesis_id", "mechanism", "direction", "tier", "level", "lines_of_evidence",
                   "genetic_score", "max_phase_in_disease", "modality", "tractability", "safety_clues",
                   "biggest_uncertainty", "pub_count", "status", "source_ids"],
    "TPP": ["hypothesis_id", "soc_gap_at_launch", "minimum_specs", "holy_grail_specs", "must_haves",
            "expansion_opportunity", "source_ids", "version"],
    "RedTeam": ["hypothesis_id", "verdict", "revise_target", "risk", "kill_criterion", "cheapest_test",
                "source_ids", "premortem"],
    "Judge": ["hypothesis_id", "red_team_is", "decision", "risk", "holds", "severity", "why", "reasoning"],
    "Citations": ["agent", "hypothesis_id", "claim_id", "source_id", "source_date", "supports", "action"],
    "Restarts": ["agent", "hypothesis_id", "attempt", "hallucinated_claims"],
    "NewEvidence": ["status", "id", "reason"],
    "ToolCalls": ["#", "agent", "checker", "tool", "args", "results", "ids", "published", "error", "ms"],
    "Claims": ["claim_id", "agent", "hypothesis_id", "kind", "confidence", "text", "source_ids"],
    "Events": ["#", "event", "agent", "hypothesis_id", "detail"],
    "Meta": ["key", "value"],
}

# Per agent usage: tokens + optional USD (env PRICE_<MODEL>_IN / _OUT, USD per 1M tokens).


def _join(xs) -> str:
    return "; ".join(str(x) for x in xs or [])


def _srcs(claims) -> str:
    return _join(dict.fromkeys(s for c in claims or [] for s in c.get("source_ids", [])))


class AnalysisWorkbook:
    def __init__(self, ctx, path: Path):
        self.ctx = ctx
        self.path = Path(path)
        self.why_now: dict | None = None
        self.cards: list[dict] = []
        self.grades: dict = {}
        self.tpp: dict[str, dict] = {}
        self.tpp_version: dict[str, int] = {}
        self.verdicts: dict[str, dict] = {}
        self.rejected: dict[str, str] = {}
        self.claims: dict[str, dict] = {}
        self.events: list[tuple] = []
        self.judgments: dict[str, dict] = {}
        self.tool_calls: list[dict] = []
        self.new_evidence: list[dict] = []
        self.citations: dict[tuple, list] = {}
        self.restarts: list[list] = []

    def attach(self) -> "AnalysisWorkbook":
        self.ctx.bus.subscribe(self.on_event)
        return self

    # ------------------------------------------------------------------ events
    def on_event(self, event: str, p: dict) -> None:
        agent, hid = p.get("agent"), p.get("hypothesis_id")
        if event == "claim_added":
            c = p["claim"]
            self.claims[c["id"]] = {**c, "hypothesis_id": hid}
            return  # claims are written with the next agent_done
        if event == "tool_call":
            self.tool_calls.append(p)
            return  # its own sheet; written with the next agent_done
        if event == "citation_check":  # latest check per agent/card wins (after restarts)
            self.citations[(agent, hid)] = p.get("entries") or []
            return
        if event == "node_restart":
            self.restarts.append([agent, hid or "", p.get("attempt"), ", ".join(p.get("claims") or [])])
            return
        if event == "new_evidence":
            self.new_evidence += p.get("outcomes") or []
        detail = {"kill": lambda: p.get("kill_criterion"), "revise": lambda: p.get("revise_target"),
                  "card_rejected": lambda: p.get("reason"), "warning": lambda: p.get("msg"),
                  "signal": lambda: p.get("name")}.get(event, lambda: "")()
        self.events.append((len(self.events) + 1, event, agent, hid, detail))
        if event == "agent_done":
            out = p["output"]
            if agent == "a1_disease":
                self.why_now = out
            elif agent == "a2_mechanism":
                self.cards, self.grades = out, p.get("grades", {})
            elif agent == "a3_profile":
                self.tpp[hid] = out
                self.tpp_version[hid] = self.tpp_version.get(hid, 0) + 1
            elif agent == "a6_redteam":
                self.verdicts[hid] = out
            elif agent == "a6b_judge":
                self.judgments[hid] = out
        elif event == "card_rejected":
            self.rejected[hid] = p.get("reason", "")
        if event in ("agent_done", "card_rejected", "kill", "revise", "agent_started"):
            self.write()

    # ------------------------------------------------------------------ write
    def write(self) -> Path:
        wb = Workbook()
        wb.remove(wb.active)
        rows = self._rows()
        for name, header in SHEETS.items():
            ws = wb.create_sheet(name)
            ws.append(header)
            for cell in ws[1]:
                cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEADER_FILL
            for r in rows[name]:
                ws.append(r)
            ws.freeze_panes = "A2"
            for i, h in enumerate(header, 1):
                width = max([len(h)] + [len(str(r[i - 1])) for r in rows[name] if i - 1 < len(r)])
                ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
            if name in ("Hypotheses", "RedTeam", "Judge"):
                col = header.index({"Hypotheses": "status", "RedTeam": "verdict", "Judge": "decision"}[name])
                for row in ws.iter_rows(min_row=2):
                    color = VERDICT_FILL.get(str(row[col].value).split(" ")[0])
                    if color:
                        for cell in row:
                            cell.fill = PatternFill("solid", fgColor=color)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=self.path.parent)
        os.close(fd)
        wb.save(tmp)
        os.replace(tmp, self.path)
        return self.path

    def _rows(self) -> dict[str, list[list]]:
        R: dict[str, list[list]] = {k: [] for k in SHEETS}
        wn = self.why_now or {"shifts": [], "segments": []}
        for s in wn["shifts"]:
            R["WhyNow"].append([s["on"], s["type"], _join(s["effect_on"]), s["summary"],
                                _srcs(s["claims"]), max((c["confidence"] for c in s["claims"]), default="")])
        for g in wn["segments"]:
            R["Segments"].append([g["name"], _join(g["inclusion_criteria"]), _srcs(g["claims"])])

        for c in self.cards:
            hid, gr = c["id"], self.grades.get(c["id"], {})
            v = self.verdicts.get(hid, {}).get("verdict")
            status = v or ("rejected" if hid in self.rejected else
                           "profiled" if hid in self.tpp else "carded")
            if hid in self.rejected:
                status = f"rejected ({self.rejected[hid]})"
            R["Hypotheses"].append([gr.get("rank", ""), hid, c["mechanism"], c["direction"], c["evidence_tier"],
                                    c["validation_level"], gr.get("lines", ""), gr.get("genetic", ""),
                                    gr.get("phase_in_disease", ""), c["modality"], c["tractability"],
                                    _join(c["safety_clues"]), c["biggest_uncertainty"], gr.get("pub_count", ""),
                                    status, _srcs(c["claims"])])

        for hid, t in self.tpp.items():
            R["TPP"].append([hid, t["soc_gap_at_launch"], _join(t["minimum_specs"]), _join(t["holy_grail_specs"]),
                             _join(t["must_haves"]), t["expansion_opportunity"], _srcs(t["claims"]),
                             self.tpp_version.get(hid, 1)])

        for hid, v in self.verdicts.items():
            for r in v["risks"] or [{}]:
                R["RedTeam"].append([hid, v["verdict"], v.get("revise_target") or "", r.get("text", ""),
                                     r.get("kill_criterion", ""), r.get("cheapest_test", ""),
                                     _srcs(r.get("claims")), v["premortem"]])

        for hid, j in self.judgments.items():
            for r in j["rulings"] or [{}]:
                R["Judge"].append([hid, j["red_team_is"], j["decision"], r.get("risk", ""), r.get("holds", ""),
                                   r.get("severity", ""), r.get("why", ""), j["reasoning"]])
        for es in self.citations.values():
            for e in es:
                R["Citations"].append([e.get("agent"), e.get("hypothesis_id") or "", e["claim_id"], e["source_id"],
                                       e.get("source_date") or "", e["supports"], e["action"]])
        R["Restarts"] = list(self.restarts)
        R["NewEvidence"] = [[o.get("status"), o.get("id"), o.get("reason")] for o in self.new_evidence]
        for i, t in enumerate(self.tool_calls, 1):
            R["ToolCalls"].append([i, t.get("agent"), t.get("checker") or "", t.get("tool"),
                                   str(t.get("args"))[:300], t.get("n"), _join(t.get("ids"))[:500],
                                   _join(t.get("dates"))[:300], t.get("error") or "", t.get("ms")])

        for c in self.claims.values():
            R["Claims"].append([c["id"], c["agent"], c.get("hypothesis_id") or "", c["kind"], c["confidence"],
                                c["text"], _join(c["source_ids"])])
        R["Events"] = [list(e) for e in self.events]

        cfg = self.ctx.config
        R["Meta"] = [["run_id", self.ctx.run_id], ["disease", f"{cfg.disease} ({cfg.disease_id})"],
                     ["as_of", cfg.as_of.isoformat()], ["mode", cfg.mode], ["profile", cfg.profile],
                     ["audience", cfg.audience]]
        usage = getattr(self.ctx.llm, "usage", {}) or {}
        total = 0.0
        for agent, u in usage.items():
            usd = _usd(u)
            total += usd
            R["Meta"].append([f"{agent}", f"model={u.model} calls={u.calls} in={u.input_tokens} "
                              f"cached={u.cached_tokens} out={u.output_tokens} usd={usd:.4f} "
                              f"latency_ms={u.latency_ms}"])
        R["Meta"].append(["total_usd (person 3 agents)", round(total, 4)])
        return R


def _usd(u) -> float:
    key = u.model.upper().replace("-", "_").replace(".", "_")
    pin = float(os.getenv(f"PRICE_{key}_IN", 0))
    pout = float(os.getenv(f"PRICE_{key}_OUT", 0))
    return (u.input_tokens * pin + u.output_tokens * pout) / 1e6
