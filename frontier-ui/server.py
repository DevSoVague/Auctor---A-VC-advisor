"""Local Auctor workspace. No web dependencies; existing engine is unchanged."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse, base64, datetime, json, mimetypes, os, re, subprocess, threading, uuid

ROOT = Path(__file__).resolve().parent
_IN_REPO = ROOT.parent / 'workflow'  # frontier-ui inside the repo
_INSIDE = _IN_REPO.is_dir() and (ROOT.parent / 'agents').is_dir()  # the repo root holds agents/ and workflow/
ENGINE = Path(os.environ.get('FRONTIER_ENGINE', _IN_REPO if _INSIDE else ROOT.parent / 'auctor' / 'workflow')).expanduser().resolve()
RUNS = Path(os.environ.get('FRONTIER_RUNS', ENGINE / 'runs')).expanduser().resolve()
JOBS = {}
LOCK = threading.Lock()

def read_json(path, fallback=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return fallback

def run_path(value):
    if not re.fullmatch(r'[\w-]{1,100}', value): raise ValueError('Invalid run identifier')
    path = RUNS / value
    if not path.is_dir(): raise ValueError('Run not found')
    return path

def run_list():
    rows = []
    for p in RUNS.iterdir() if RUNS.exists() else []:
        if not p.is_dir() or not re.fullmatch(r'[\w-]+', p.name): continue
        files = [f for f in (p/'result.json', p/'live.jsonl', p/'report.md') if f.exists()]
        if not files: continue
        modified = max(f.stat().st_mtime for f in files)
        rows.append({'id': p.name, 'modified': modified, 'complete': (p/'result.json').exists(), 'report': (p/'report.md').exists()})
    return sorted(rows, key=lambda r:r['modified'], reverse=True)

def execute(job, args, pitch):
    try:
        before = {r['id'] for r in run_list()}
        if args['mode'] == 'live':
            # A1 can write evidence. Take the required database snapshot first.
            backup = ROOT / 'backups'; backup.mkdir(exist_ok=True)
            destination = backup / (datetime.datetime.now().strftime('%Y%m%d-%H%M%S-') + job + '.dump')
            subprocess.run(['pg_dump', '-Fc', os.environ.get('DATABASE_URL', 'postgresql://frontier:frontier@localhost:5433/frontier'), '-f', str(destination)], check=True, capture_output=True)
        python = os.environ.get('FRONTIER_PYTHON', '/opt/anaconda3/envs/hack/bin/python')
        if not Path(python).exists(): python = 'python3'
        cmd = [python, '-m', 'auctor', 'run', '--llm', args['mode'], '--as-of', args['as_of'], '--max-cards', str(args['cards']), '--disease', args['disease'], '--audience', 'specialist', '--compact']
        if args['mode'] == 'live': cmd += ['--search', 'evstore', '--tools']
        if pitch: cmd += ['--pitch', str(pitch)]
        process = subprocess.Popen(cmd, cwd=ENGINE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            with LOCK:
                JOBS[job]['log'] = (JOBS[job]['log'] + line)[-30000:]
                fresh = [r for r in run_list() if r['id'] not in before]
                if fresh: JOBS[job]['run'] = fresh[0]['id']
        code = process.wait()
        with LOCK: JOBS[job]['status'] = 'complete' if code == 0 else 'failed'
    except Exception as exc:
        with LOCK:
            JOBS[job]['status'] = 'failed'
            JOBS[job]['log'] += '\n' + str(exc)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def reply(self, data, code=200, kind='application/json', download=None):
        body = json.dumps(data, default=str).encode() if kind == 'application/json' else data
        self.send_response(code); self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if download: self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        try:
            url = urlparse(self.path); q = parse_qs(url.query)
            if url.path == '/api/runs': return self.reply(run_list())
            if url.path == '/api/job':
                with LOCK: data = dict(JOBS.get(q.get('id',[''])[0], {'status':'unknown'}))
                return self.reply(data)
            if url.path in ('/api/run', '/api/events', '/api/artifact'):
                folder = run_path(q.get('id', [''])[0])
                if url.path == '/api/run':
                    result = read_json(folder/'result.json', {})
                    return self.reply({'result': result, 'artifacts': [f.name for f in folder.iterdir() if f.name in ('report.md','result.json','analysis.xlsx','chat.md','steps.md','failures.log')], 'report': (folder/'report.md').read_text() if (folder/'report.md').exists() else ''})
                if url.path == '/api/events':
                    offset = max(0, int(q.get('offset',['0'])[0])); events = []
                    file = folder/'live.jsonl'
                    if file.exists():
                        with file.open('rb') as f:
                            f.seek(offset)
                            for _ in range(1000):
                                line = f.readline()
                                if not line.endswith(b'\n'): break
                                try: events.append(json.loads(line))
                                except ValueError: pass
                                offset = f.tell()
                    return self.reply({'events':events, 'offset':offset})
                name = q.get('name',[''])[0]
                if name not in ('report.md','result.json','analysis.xlsx','chat.md','steps.md','failures.log'): raise ValueError('Artifact not allowed')
                file = folder/name
                if not file.exists(): return self.reply({'error':'Artifact is not available yet'},404)
                return self.reply(file.read_bytes(), kind=mimetypes.guess_type(name)[0] or 'application/octet-stream', download=name)
            files = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            name = files.get(url.path)
            if not name: return self.reply({'error':'Not found'},404)
            return self.reply((ROOT/'static'/name).read_bytes(), kind=mimetypes.guess_type(name)[0] or 'text/plain')
        except (ValueError,OSError) as exc: self.reply({'error':str(exc)},400)
    def do_POST(self):
        # Only same-origin browser requests can trigger a local analysis.
        if self.headers.get('Origin') and self.headers['Origin'] != 'http://' + self.headers.get('Host',''):
            return self.reply({'error':'Origin denied'},403)
        if self.path != '/api/start': return self.reply({'error':'Not found'},404)
        try:
            size = int(self.headers.get('Content-Length',0))
            if not 0 < size <= 29_000_000: raise ValueError('Upload limit is 20 MB')
            if 'application/json' not in self.headers.get('Content-Type',''): raise ValueError('JSON required')
            data = json.loads(self.rfile.read(size))
            mode = data.get('mode','stub')
            if mode not in ('stub','live'): raise ValueError('Unknown execution mode')
            cutoff = data.get('as_of') or datetime.date.today().isoformat(); datetime.date.fromisoformat(cutoff)
            cards = int(data.get('cards',1))
            if not 1 <= cards <= 6: raise ValueError('Choose 1–6 hypotheses')
            disease = str(data.get('disease','COPD')).strip()
            if disease != 'COPD': raise ValueError('This pipeline is configured for COPD')
            with LOCK:
                if any(j['status']=='running' for j in JOBS.values()): return self.reply({'error':'An analysis is already running'},409)
                job = uuid.uuid4().hex
                JOBS[job] = {'status':'running','log':'Starting analysis…\n','run':None}
            pitch = None
            if data.get('file'):
                ext = Path(data.get('filename','')).suffix.lower()
                if ext not in ('.pdf','.docx','.txt','.md'): raise ValueError('Use PDF, DOCX, TXT or MD')
                raw = base64.b64decode(data['file'],validate=True)
                if len(raw)>20*1024*1024: raise ValueError('Upload exceeds 20 MB')
                folder = ROOT/'uploads'; folder.mkdir(exist_ok=True)
                pitch = folder/(job+ext); pitch.write_bytes(raw)
            threading.Thread(target=execute,args=(job,{'mode':mode,'as_of':cutoff,'cards':cards,'disease':disease},pitch),daemon=True).start()
            self.reply({'job':job},202)
        except (ValueError,OSError) as exc:
            if 'job' in locals():
                with LOCK: JOBS[job]['status']='failed'
            self.reply({'error':str(exc)},400)

def port_open(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def start_backend(engine, langflow_port):
    """One command for everything: the evidence-store database (Docker) and Langflow with the Auctor components.
    Anything already running is reused and left alone; only what this process started is stopped on exit."""
    started = []
    store = engine.parent / 'evidence-store'
    if not port_open(5433) and (store / 'docker-compose.yml').exists():
        print('Backend: starting the evidence-store database (Docker, :5433)…', flush=True)
        r = subprocess.run(['docker', 'compose', 'up', '-d'], cwd=store, capture_output=True, text=True)
        print('Backend: database ' + ('up' if r.returncode == 0 else 'FAILED: ' + (r.stderr or r.stdout).strip()[-300:]), flush=True)
    if port_open(langflow_port):
        print(f'Backend: Langflow already running on :{langflow_port} (reused)', flush=True)
        return started
    script = engine / 'langflow' / 'run_langflow.sh'
    if not script.exists():
        print(f'Backend: {script} not found; Langflow not started', flush=True)
        return started
    log = ROOT / 'langflow.log'
    print(f'Backend: starting Langflow on :{langflow_port} (log: {log})…', flush=True)
    proc = subprocess.Popen(['bash', str(script)], cwd=script.parent, env={**os.environ, 'PORT': str(langflow_port)},
                            stdout=log.open('a'), stderr=subprocess.STDOUT, start_new_session=True)
    started.append(proc)

    def wait_ready():
        import time
        for _ in range(240):  # Langflow takes a while to load its components
            if proc.poll() is not None:
                print(f'Backend: Langflow exited (code {proc.returncode}); see {log}', flush=True); return
            if port_open(langflow_port):
                print(f'Backend: Langflow ready on http://127.0.0.1:{langflow_port}', flush=True); return
            time.sleep(1)
        print(f'Backend: Langflow still not answering after 4 min; see {log}', flush=True)
    threading.Thread(target=wait_ready, daemon=True).start()
    return started


def stop_backend(started):
    import signal
    for proc in started:
        if proc.poll() is None:
            print('Backend: stopping Langflow…', flush=True)
            try:
                os.killpg(proc.pid, signal.SIGTERM); proc.wait(timeout=20)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass


if __name__ == '__main__':
    import signal
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=7880)
    parser.add_argument('--engine',type=Path,default=ENGINE,help='Existing workflow/ folder')
    parser.add_argument('--runs',type=Path,default=None,help='Existing workflow run directory to follow')
    parser.add_argument('--langflow-port',type=int,default=int(os.environ.get('LANGFLOW_PORT', 7870)),help='Langflow backend port')
    parser.add_argument('--no-backend',action='store_true',help='Do not start the database or Langflow')
    args=parser.parse_args()
    ENGINE=args.engine.expanduser().resolve()
    RUNS=args.runs.expanduser().resolve() if args.runs else (Path(os.environ['FRONTIER_RUNS']).expanduser().resolve() if os.environ.get('FRONTIER_RUNS') else ENGINE/'runs')
    if not RUNS.is_dir(): parser.error(f'Run directory does not exist: {RUNS}')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    started=[] if args.no_backend else start_backend(ENGINE, args.langflow_port)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    print(f'Auctor workspace: http://127.0.0.1:{args.port}\nFollowing existing workflow: {RUNS}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop_backend(started)
