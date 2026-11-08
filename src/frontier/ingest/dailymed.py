"""Earlier FDA label versions from DailyMed, so backtests see the marketed drugs (person 1).

fda.py stores one row per marketed drug, dated by its newest label, Part D release or
industry source (all in 2025). A backtest with an earlier cutoff, such as 2025-01-01,
then sees no marketed drug at all. DailyMed keeps every published version of a label.

For each fda row in the store, this reads the label's version history from DailyMed
(by its set id), and stores every version published 2023-01-01 to 2025-12-31 whose
indications mention COPD, as another version of the same document:

    id fda:<application number>, version_label dailymed-v<n>, source_date = the date
    DailyMed published that version, fields.sections = that version's label text.

A version published on the same day as a row fda.py wrote is skipped (fda.py's row
holds the same label and more). Versions before 2023 are not stored (team whiteboard:
2023-2025 only), and nor are versions before a drug's COPD approval, since their
indications don't mention COPD. Raw files are kept in data/fda/dailymed/ (git-ignored).

    uv run python -m frontier.ingest.dailymed      (after fda)
"""

from __future__ import annotations

import hashlib
import io
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import httpx

from frontier.core.config import REPO_ROOT, Settings
from frontier.ingest import fda
from frontier.ingest.window import in_window

HISTORY = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}/history.json"
VERSION_ZIP = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm"
RAW_DIR = REPO_ROOT / "data" / "fda" / "dailymed"
V3 = "{urn:hl7-org:v3}"
# LOINC section codes in SPL, mapped to the openFDA field names fda.py uses.
LOINC = {
    "43679-0": "mechanism_of_action",
    "34067-9": "indications_and_usage",
    "34068-7": "dosage_and_administration",
    "34070-3": "contraindications",
    "43685-7": "warnings_and_cautions",
    "34084-4": "adverse_reactions",
    "34092-7": "clinical_studies",
}


def versions(history: dict) -> list[tuple[int, date]]:
    """(version, published date) pairs from a DailyMed history.json response."""
    out = []
    for h in history.get("data", {}).get("history", []):
        published = datetime.strptime(h["published_date"], "%b %d, %Y").date()
        out.append((int(h["spl_version"]), published))
    return sorted(out)


def _section_text(section: ET.Element) -> str:
    """All text of an SPL section and its subsections, without the highlights excerpts."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == f"{V3}excerpt":
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(section)
    return " ".join(" ".join(parts).split())


def parse_spl(xml: bytes) -> dict:
    """Label sections (openFDA field names) and the effective date of one SPL document."""
    root = ET.fromstring(xml)
    sections: dict[str, str] = {}
    for sec in root.iter(f"{V3}section"):
        code = sec.find(f"{V3}code")
        key = LOINC.get(code.get("code") if code is not None else "")
        if key and key not in sections:  # the first (outermost) section with the code
            text = _section_text(sec)
            if text:
                sections[key] = text
    eff = root.find(f"{V3}effectiveTime")
    value = (eff.get("value") or "")[:8] if eff is not None else ""
    effective = f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else None
    return {"sections": sections, "label_effective": effective}


def spl_from_zip(data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        (name,) = [n for n in z.namelist() if n.lower().endswith(".xml")]
        return z.read(name)


def rows_for(current: dict, history: list[tuple[int, date]], spls: dict[int, dict]) -> tuple:
    """Rows for one drug's earlier label versions, and a report line per skipped version.

    current: the newest fda row for the drug (its fields give names, class, set id).
    spls: version -> parse_spl() result, for the versions that were downloaded."""
    f = current["fields"]
    rows, report = [], []
    for version, published in history:
        if not in_window(published) or version not in spls:
            continue
        spl = spls[version]
        if not fda.COPD.search(spl["sections"].get("indications_and_usage", "")):
            report.append(f"{f['brand']} v{version} ({published}): no COPD indication, skipped")
            continue
        label = {
            **{k: f.get(k) for k in ("brand", "generic", "manufacturer", "application_number")},
            "sections": spl["sections"],
        }
        fields = {
            k: f.get(k)
            for k in (
                "brand",
                "generic",
                "manufacturer",
                "application_number",
                "pharm_class_epc",
                "pharm_class_moa",
                "set_id",
                "drug_class",
            )
        }
        url = f"{VERSION_ZIP}?type=zip&setid={f['set_id']}&version={version}"
        fields.update(
            {
                "label_effective": spl["label_effective"],
                "spl_version": version,
                "dailymed_published": published.isoformat(),
                "sections": spl["sections"],
                "source_urls": [url],
            }
        )
        title = f"{f['brand']} ({f['generic']}): FDA label"
        body = (
            fda.body_of(label, None, f.get("drug_class") or "")
            + f"\nDailyMed label version {version}, published {published.isoformat()}."
        )
        rows.append(
            {
                "id": current["id"],
                "source_date": published,
                "version_label": f"dailymed-v{version}",
                "title": title,
                "body": body,
                "url": url,
                "fields": fields,
                "text_hash": hashlib.sha256(f"{title}\n{body}".encode()).hexdigest(),
            }
        )
    return rows, report


class Client:
    def __init__(self) -> None:
        self.http = httpx.Client(
            timeout=120, follow_redirects=True, headers={"User-Agent": fda.USER_AGENT}
        )

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(4):
            resp = self.http.get(url, params=params)
            if resp.status_code == 200:
                return resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(2**attempt)
        resp.raise_for_status()
        return resp

    def history(self, setid: str) -> list[tuple[int, date]]:
        out, page = [], 1
        while True:
            data = self._get(HISTORY.format(setid=setid), {"pagesize": 100, "page": page}).json()
            out += versions(data)
            meta = data.get("metadata", {})
            if int(meta.get("current_page", 1)) >= int(meta.get("total_pages", 1)):
                return sorted(out)
            page += 1

    def spl(self, setid: str, version: int) -> dict | None:
        path = RAW_DIR / setid / f"v{version}.xml"
        if not path.exists():
            resp = self._get(VERSION_ZIP, {"type": "zip", "setid": setid, "version": version})
            if resp.content[:2] != b"PK":  # not a zip: DailyMed answered with a page
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(spl_from_zip(resp.content))
            time.sleep(0.5)
        return parse_spl(path.read_bytes())


def main() -> int:
    import psycopg
    from psycopg.rows import dict_row

    client = Client()
    with psycopg.connect(Settings().database_url) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, source_date, version_label, fields FROM documents"
                " WHERE source = 'fda' ORDER BY id, source_date"
            )
            docs = cur.fetchall()
        taken = {(d["id"], d["source_date"]) for d in docs if d["version_label"].startswith("fda-")}
        newest = {d["id"]: d for d in docs if d["version_label"].startswith("fda-")}
        stored = 0
        for doc_id, current in sorted(newest.items()):
            setid = current["fields"].get("set_id")
            if not setid:
                print(f"{doc_id}: no set id, skipped")
                continue
            history = [(v, p) for v, p in client.history(setid) if in_window(p)]
            spls = {v: s for v, _ in history if (s := client.spl(setid, v)) is not None}
            missing = [v for v, _ in history if v not in spls]
            rows, report = rows_for(current, history, spls)
            rows = [r for r in rows if (r["id"], r["source_date"]) not in taken]
            fda.upsert(conn, rows)
            stored += len(rows)
            for line in report:
                print(line)
            print(
                f"{current['fields']['brand']}: {len(history)} versions in 2023-2025,"
                f" {len(rows)} stored"
                + (f", {len(missing)} not downloadable: {missing}" if missing else "")
                + (f", earliest {min(r['source_date'] for r in rows)}" if rows else "")
            )
    print(f"done: {stored} earlier label versions stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
