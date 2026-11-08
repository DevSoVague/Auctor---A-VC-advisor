"""Push flows/person3_analysis.json into a running Langflow (create or overwrite), print the URL.

    conda run -n langflow python sync_flow.py                         # the 7-node flow, server on :7860
    conda run -n langflow python sync_flow.py flows/person3_steps.json   # the step-by-step flow
    PORT=7861 conda run -n langflow python sync_flow.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASE = f"http://127.0.0.1:{os.getenv('PORT', '7860')}"


def main() -> None:
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "flows" / "person3_analysis.json"
    flow = json.loads(path.read_text())
    c = httpx.Client(timeout=60)
    try:
        tok = c.get(f"{BASE}/api/v1/auto_login").json()["access_token"]
    except httpx.ConnectError:
        raise SystemExit(f"Langflow is not running on {BASE}. Start it with ./run_langflow.sh first.")
    h = {"Authorization": f"Bearer {tok}"}
    body = {"name": flow["name"], "description": flow.get("description"), "data": flow["data"]}
    flows = c.get(f"{BASE}/api/v1/flows/", headers=h, params={"get_all": True}).json()
    same = [f for f in flows if isinstance(f, dict) and f.get("name") == flow["name"]]
    if same:
        fid = same[0]["id"]
        r = c.patch(f"{BASE}/api/v1/flows/{fid}", headers=h, json=body)
        for extra in same[1:]:  # remove duplicates from earlier imports
            c.delete(f"{BASE}/api/v1/flows/{extra['id']}", headers=h)
    else:
        r = c.post(f"{BASE}/api/v1/flows/", headers=h, json=body)
        fid = r.json().get("id")
    r.raise_for_status()
    stored = c.get(f"{BASE}/api/v1/flows/{fid}", headers=h).json()["data"]
    print(f"synced: {len(stored['nodes'])} nodes, {len(stored['edges'])} connections")
    print(f"open:   http://localhost:{os.getenv('PORT', '7860')}/flow/{fid}")


if __name__ == "__main__":
    main()
