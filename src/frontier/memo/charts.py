"""Memo charts as PNG files for Word and PDF (owner: person 5).

Static, print-first charts, so there is no hover layer. Colors come from the dataviz reference
palette (light mode, validated): the pursuit forms use categorical slots 1-3, which pass the
all-pairs colorblind check; counts use the single-hue blue ramp; killed hypotheses are gray.
Identity is never color alone: every mark has a direct text label, and forms also differ by
marker shape.

    opportunity_map     every hypothesis: evidence strength vs crowding, size = best fit
    return_risk_grid    each pursuit on a 3x3 upside-vs-risk grid
    catalyst_timeline   dated events in the next 12 months
    landscape_matrix    competitor programs: mechanism x development stage, counts
"""

import re
import textwrap
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # file output only; no display needed

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from frontier.memo.draft import MemoDraft  # noqa: E402
from frontier.memo.models import MemoInput  # noqa: E402
from frontier.memo.scoring import RATINGS, Scorecard, score  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
FORM_COLOR = {"invest": "#2a78d6", "access": "#eb6834", "build": "#1baf7a"}  # slots 1-3
FORM_MARKER = {"invest": "o", "access": "s", "build": "^"}
BLUE_RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#104281"]
STAGES = ["Preclinical", "Phase 1", "Phase 2", "Phase 3", "Approved", "Other"]
DPI = 200

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 9,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "axes.titlelocation": "left",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
    }
)


def _finish(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _short(text: str, width: int = 26) -> str:
    return textwrap.shorten(text, width=width, placeholder="…")


def opportunity_map(inp: MemoInput, path: Path) -> Path:
    """Stronger evidence to the right, less crowded higher up: the top-right is the sweet spot."""
    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    ax.axvspan(2.5, 4.6, ymin=0.5, ymax=1, color="#f0efec", zorder=0)
    ax.text(
        4.5,
        0.03,
        "Sweet spot: strong evidence,\nopen field",
        ha="right",
        va="top",
        color=INK_2,
        fontsize=8,
        style="italic",
    )
    seen_status = set()
    for h in inp.hypotheses:
        s: Scorecard = score(h, inp)
        crowd = s.crowding if s.crowding is not None else 0.5
        x = 5 - s.tier + min(s.evidence_lines or 0, 3) * 0.08  # converging lines nudge right
        size = 60 + 340 * (s.best_fit or 0.0)
        killed = h.status != "survives"
        label = "Killed or incomplete" if killed else "Survives"
        ax.scatter(
            x,
            crowd,
            s=size,
            zorder=3,
            label=None if label in seen_status else label,
            facecolors="none" if killed else FORM_COLOR["invest"],
            edgecolors=MUTED if killed else SURFACE,
            linewidths=1.5 if killed else 2,
            hatch="////" if killed else None,
        )
        seen_status.add(label)
        ax.annotate(
            f"{h.id} {_short(h.card.mechanism, 20)}",
            (x, crowd),
            xytext=(9, 4),
            textcoords="offset points",
            fontsize=8,
            color=MUTED if killed else INK,
        )
    ax.set_xlim(0.6, 4.6)
    ax.set_ylim(1.05, -0.05)  # less crowded at the top
    ax.set_xticks([1, 2, 3, 4], ["Tier 4", "Tier 3", "Tier 2", "Tier 1"])
    ax.set_xlabel("Evidence strength (stronger to the right)")
    ax.set_ylabel("Crowding (0 = open field)")
    ax.grid(color=GRID, linewidth=0.6, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("Opportunity map: every hypothesis (bubble size = best asset fit)")
    ax.legend(frameon=False, fontsize=8, loc="lower left", markerscale=0.5)
    return _finish(fig, path)


def return_risk_grid(draft: MemoDraft, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(4.6, 3.6))
    for i in range(3):
        for j in range(3):
            good = j - i >= 1  # upside above risk
            ax.add_patch(
                plt.Rectangle(
                    (i - 0.5, j - 0.5),
                    1,
                    1,
                    facecolor="#f0efec" if good else SURFACE,
                    edgecolor=GRID,
                    linewidth=1,
                )
            )
    # Markers carry the pursuit number; names go in the key below, so a shared cell never
    # stacks two labels on top of each other.
    counts: dict[tuple[int, int], int] = {}
    for n, p in enumerate(draft.pursuits, 1):
        cell = (RATINGS.index(p.risk), RATINGS.index(p.upside))
        k = counts.get(cell, 0)
        counts[cell] = k + 1
        x, y = cell[0] - 0.28 + 0.28 * k, cell[1]
        ax.scatter(
            x,
            y,
            s=260,
            marker=FORM_MARKER[p.form],
            color=FORM_COLOR[p.form],
            edgecolors=SURFACE,
            linewidths=2,
            zorder=3,
            label=f"{n}. {p.form.title()}: {_short(p.name, 40)}",
        )
        ax.text(
            x,
            y,
            str(n),
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            color="#ffffff",
            zorder=4,
        )
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.2),
        frameon=False,
        fontsize=7.5,
        markerscale=0.6,
        handletextpad=0.4,
    )
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(-0.5, 2.5)
    ax.set_xticks(range(3), [r.title() for r in RATINGS])
    ax.set_yticks(range(3), [r.title() for r in RATINGS])
    ax.set_xlabel("Risk")
    ax.set_ylabel("Upside")
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_title("Is the return worth the risk?")
    ax.text(
        -0.45, 2.42, "shaded: upside above risk", fontsize=7, color=INK_2, va="top", style="italic"
    )
    return _finish(fig, path)


def catalyst_timeline(draft: MemoDraft, path: Path, as_of: date) -> Path | None:
    if not draft.catalysts:
        return None
    events = draft.catalysts[:8]
    fig, ax = plt.subplots(figsize=(6.4, 1.5 + 0.1 * len(events)))
    dates = [c.on for c in events]
    ax.axhline(0, color=AXIS, linewidth=1, zorder=1)
    ax.scatter(
        dates,
        [0] * len(dates),
        s=60,
        color=FORM_COLOR["invest"],
        edgecolors=SURFACE,
        linewidths=2,
        zorder=3,
    )
    for i, c in enumerate(events):
        up = 1 if i % 2 == 0 else -1
        ax.annotate(
            f"{c.on:%b %Y}\n" + textwrap.fill(_short(c.what, 60), 30),
            (c.on, 0),
            xytext=(0, 16 * up),
            textcoords="offset points",
            ha="center",
            va="bottom" if up > 0 else "top",
            fontsize=7,
            color=INK,
        )
    start = min(as_of, *dates)
    ax.set_xlim(
        start - timedelta(days=15), max(start + timedelta(days=365), *dates) + timedelta(days=15)
    )
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.set_ylim(-0.6, 1)
    ax.set_yticks([])
    for side in ("left", "right", "top"):
        ax.spines[side].set_visible(False)
    ax.set_title("Upcoming catalysts: the next 12 months")
    return _finish(fig, path)


def _stage(text: str) -> str:
    t = text.lower()
    for pattern, name in (
        (r"approv|marketed|launched", "Approved"),
        (r"phase\s*(3|iii)", "Phase 3"),
        (r"phase\s*(2|ii)", "Phase 2"),
        (r"phase\s*(1|i)\b", "Phase 1"),
        (r"pre-?clinical|discovery", "Preclinical"),
    ):
        if re.search(pattern, t):
            return name
    return "Other"


def landscape_rows(inp: MemoInput) -> list[dict]:
    """One row per competitor program across surviving hypotheses (an asset counted once)."""
    rows: dict[str, dict] = {}
    for h in inp.survivors:
        if not h.landscape:
            continue
        for p in (*h.landscape.programs, *h.landscape.orthogonal):
            rows.setdefault(
                p.asset.lower(),
                {
                    "mechanism": p.mechanism,
                    "stage": _stage(p.stage),
                    "program": p,
                    "hypothesis_id": h.id,
                },
            )
    return list(rows.values())


def landscape_matrix(inp: MemoInput, path: Path) -> Path | None:
    rows = landscape_rows(inp)
    if not rows:
        return None
    mechs = sorted({r["mechanism"] for r in rows})[:10]
    grid = [
        [sum(1 for r in rows if r["mechanism"] == m and r["stage"] == s) for s in STAGES]
        for m in mechs
    ]
    peak = max(max(max(r) for r in grid), 4)  # small counts stay light on the ramp
    fig, ax = plt.subplots(figsize=(6.4, 0.9 + 0.38 * len(mechs)))
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    ax.imshow(
        [[v if v else float("nan") for v in r] for r in grid],
        cmap=cmap,
        vmin=0,
        vmax=peak,
        aspect="auto",
    )
    for i, r in enumerate(grid):
        for j, v in enumerate(r):
            if v:
                ax.text(
                    j,
                    i,
                    str(v),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#ffffff" if v / peak > 0.5 else INK,
                )
    ax.set_xticks(range(len(STAGES)), STAGES)
    ax.set_yticks(range(len(mechs)), [_short(m, 34) for m in mechs])
    ax.set_xticks([x - 0.5 for x in range(1, len(STAGES))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(mechs))], minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="both", length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_title("Competitor landscape: programs by mechanism and stage")
    return _finish(fig, path)


def all_charts(inp: MemoInput, draft: MemoDraft, folder: Path) -> dict[str, Path]:
    """Render every chart that has data; returns name -> PNG path."""
    out = {
        "opportunity_map": opportunity_map(inp, folder / "opportunity_map.png"),
        "return_risk": return_risk_grid(draft, folder / "return_risk.png"),
        "catalysts": catalyst_timeline(draft, folder / "catalysts.png", inp.config.as_of),
        "landscape": landscape_matrix(inp, folder / "landscape.png"),
    }
    return {k: v for k, v in out.items() if v is not None}
