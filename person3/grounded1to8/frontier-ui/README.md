# Auctor research workspace

A self-contained local frontend for `grounded1to8`, with overview, hypothesis comparisons, an evidence library, a scientific report, a dedicated Monitor tab, and run history. Existing engine files are unchanged.

```bash
cd frontier-ui
python3 server.py
```

Open **http://127.0.0.1:7880** alongside your existing workflow. The UI automatically follows the most recently updated run and refreshes the Monitor and report as your pipeline writes artifacts. You can continue launching runs through your existing CLI or Langflow. Selecting a historical run pauses following; click **Follow workflow** to resume.

No frontend installation or build is required. Fonts use Google Fonts when available and fall back to local system fonts.

- Select an existing completed run to inspect real outputs.
- **New assessment** accepts an optional PDF, DOCX, TXT, or Markdown pitch. With no pitch it runs an open scan.
- Offline mode launches fixture analysis with no model spend. Live mode uses the engine's existing credentials, configured evidence store, and tools. Before live execution the server requires a successful `pg_dump` snapshot. Install `pg_dump` and start the database first; failures appear in Monitor.
- The interpreter defaults to `/opt/anaconda3/envs/hack/bin/python`, then `python3`. Set `FRONTIER_PYTHON` to override it. Existing engine dependencies must be installed in that interpreter.
- **Scientific report → Print / Save PDF** produces a paper-style report. Choose Save as PDF in the browser. Markdown and the original Excel workbook are downloadable.
- Monitor polls the append-only event stream every two seconds. Historical unmatched starts are labeled as such; they do not establish that a process is alive.
- Inputs remain in the ignored `uploads/` folder; database snapshots stay in ignored `backups/`. Remove these manually when no longer needed. The UI does not expose environment files or arbitrary filesystem paths.

The report presents engine results, confidence, uncertainty, judge decisions, and source identifiers without inventing missing findings or verification status. Citation checks are described only when present in the run artifact.

Change the port with `python3 server.py --port 7881`.

To follow another checkout running in parallel:

```bash
python3 frontier-ui/server.py --engine db1to6merged/langgraph-person3
# Or point the observer at a specific run directory:
python3 frontier-ui/server.py --runs /absolute/path/to/runs
```

The frontend uses port 7880, independently of the existing Node Monitor on 7872. Starting the frontend starts only the UI server; it does not start another analysis. The New assessment form is optional.
