import { useCallback, useEffect, useState } from "react";
import { base44 } from "@/api/base44Client";
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock, Download, FileText,
  FlaskConical, Loader2, LogOut, PauseCircle, Play, Plus, RefreshCw, ShieldCheck, XCircle,
} from "lucide-react";

// ---------------------------------------------------------------- engine calls
// Every call goes through the frontierApi backend function, which holds the engine's
// token server-side and checks the user is logged in. The browser never sees the token.
async function api(method, path, body) {
  const res = await base44.functions.invoke("frontierApi", { method, path, body });
  const out = res?.data ?? res;
  if (out?.error) throw new Error(out.error);
  if (out?.status >= 400) throw new Error(out?.data?.detail || out?.text || `Engine error ${out.status}`);
  return out;
}

const AGENTS = {
  a1_disease: "A1 · Why now (disease analysis)",
  a2_mechanism: "A2 · Mechanisms (hypothesis cards)",
  a3_profile: "A3 · Winning product profile",
  a4_inactive: "A4 · Inactive / shelved assets",
  a5_active: "A5 · Competitor landscape",
  a6_redteam: "A6 · Red team",
  a7_judge: "A7 Judge · Rules on red-team attacks",
  a7_citations: "A7 · Citation check",
};
const agentName = (a) => AGENTS[a] || a;

const STATUS = {
  running: { label: "Running", icon: Loader2, cls: "bg-blue-50 text-blue-700 ring-blue-200", spin: true },
  paused: { label: "Waiting for your review", icon: PauseCircle, cls: "bg-amber-50 text-amber-800 ring-amber-200" },
  done: { label: "Complete", icon: CheckCircle2, cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  error: { label: "Error", icon: XCircle, cls: "bg-red-50 text-red-700 ring-red-200" },
  stopped: { label: "Stopped", icon: AlertTriangle, cls: "bg-slate-100 text-slate-700 ring-slate-200" },
};

function StatusPill({ status }) {
  const s = STATUS[status] || STATUS.stopped;
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ${s.cls}`}>
      <Icon className={`h-3.5 w-3.5 ${s.spin ? "animate-spin" : ""}`} /> {s.label}
    </span>
  );
}

const btn = "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition disabled:opacity-50 disabled:pointer-events-none";
const primary = `${btn} bg-slate-900 text-white hover:bg-slate-800`;
const secondary = `${btn} bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50`;
const card = "rounded-xl bg-white ring-1 ring-slate-200 shadow-sm";

function Section({ title, children, right }) {
  return (
    <section className={`${card} p-5`}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- new analysis
function NewRun({ onStarted }) {
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({
    disease: "COPD", as_of: today, mode: "guided", audience: "generalist",
    llm: "live", profile: "dev", search: "corpus", max_cards: 0,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (e) => {
    const value = e.target.type === "number" ? Number(e.target.value) : e.target.value;
    // Test mode's sample evidence stops in 2020, so it only works with a 2021 cutoff.
    if (k === "llm") return setF({ ...f, llm: value, as_of: value === "stub" ? "2021-01-01" : today });
    setF({ ...f, [k]: value });
  };

  const start = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const out = await api("POST", "/runs", f);
      onStarted(out.data.run_id);
    } catch (x) { setErr(String(x.message || x)); } finally { setBusy(false); }
  };

  const field = "mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-slate-500 focus:outline-none";
  const label = "block text-sm font-medium text-slate-700";
  return (
    <form onSubmit={start} className={`${card} max-w-3xl p-6`}>
      <h2 className="text-xl font-semibold text-slate-900">New analysis</h2>
      <p className="mt-1 text-sm text-slate-500">
        The engine finds why now is the time, ranks mechanisms, defines the winning product, maps every
        competitor, red-teams each hypothesis and checks every citation, then recommends 2 to 3 pursuits.
      </p>
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className={label}>Disease area
          <input className={field} value={f.disease} onChange={set("disease")} />
        </label>
        <label className={label}>Evidence cutoff
          <input type="date" className={field} value={f.as_of} onChange={set("as_of")} disabled={f.llm === "stub"} />
          <span className="mt-1 block text-xs text-slate-500">
            {f.llm === "stub"
              ? "Test mode uses sample evidence up to 2020, so the cutoff is fixed at 2021-01-01."
              : "Nothing published after this date is used (backtests use an earlier date)."}
          </span>
        </label>
        <label className={label}>Review mode
          <select className={field} value={f.mode} onChange={set("mode")}>
            <option value="guided">Guided: pause for my review after each agent</option>
            <option value="single_shot">Single shot: run straight through</option>
          </select>
        </label>
        <label className={label}>Written for
          <select className={field} value={f.audience} onChange={set("audience")}>
            <option value="generalist">Generalist investor</option>
            <option value="specialist">Clinical specialist</option>
          </select>
        </label>
        <label className={label}>Evidence source
          <select className={field} value={f.search} onChange={set("search")}>
            <option value="corpus">Curated COPD corpus</option>
            <option value="database">Evidence database</option>
            <option value="both">Corpus + web</option>
          </select>
        </label>
        <label className={label}>Hypotheses to analyse in depth
          <input type="number" min={0} max={5} className={field} value={f.max_cards} onChange={set("max_cards")} />
          <span className="mt-1 block text-xs text-slate-500">0 = all ranked hypotheses.</span>
        </label>
        <label className={label}>Models
          <select className={field} value={f.llm} onChange={set("llm")}>
            <option value="live">Live models</option>
            <option value="stub">Test mode (no model calls, no cost)</option>
          </select>
        </label>
        <label className={label}>Model profile
          <select className={field} value={f.profile} onChange={set("profile")}>
            <option value="dev">Fast</option>
            <option value="final">Final (strongest models)</option>
          </select>
        </label>
      </div>
      {err && <p className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{err}</p>}
      <div className="mt-6 flex justify-end">
        <button className={primary} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Start analysis
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- review
const VERDICT = {
  yes: "bg-emerald-50 text-emerald-700", partly: "bg-amber-50 text-amber-800", no: "bg-red-50 text-red-700",
  not_found: "bg-red-50 text-red-700", after_cutoff: "bg-red-50 text-red-700",
};

function Ledger({ name, report }) {
  return (
    <div className="mt-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-slate-800">{name}</span>
        <span className="text-slate-500">{report.checked_pairs} source checks:</span>
        <span className="text-emerald-700">{report.kept} kept</span>·
        <span className="text-amber-700">{report.downgraded} downgraded</span>·
        <span className="text-red-700">{report.flagged} flagged</span>
      </div>
      {report.entries?.length > 0 && (
        <div className="max-h-56 overflow-auto rounded-md ring-1 ring-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 text-slate-500">
              <tr><th className="px-2 py-1.5">Claim</th><th className="px-2 py-1.5">Source</th><th className="px-2 py-1.5">Dated</th><th className="px-2 py-1.5">Supports</th><th className="px-2 py-1.5">Action</th></tr>
            </thead>
            <tbody>
              {report.entries.map((e, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="px-2 py-1 font-mono">{e.claim_id}</td>
                  <td className="px-2 py-1 font-mono">{e.source_id}</td>
                  <td className="px-2 py-1">{e.source_date || "—"}</td>
                  <td className="px-2 py-1"><span className={`rounded px-1.5 py-0.5 ${VERDICT[e.supports] || ""}`}>{e.supports.replace("_", " ")}</span></td>
                  <td className="px-2 py-1">{e.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.invalid_claims?.length > 0 && (
        <p className="mt-1 text-xs text-red-700">
          {report.invalid_claims.length} claim(s) break the evidence rules: {report.invalid_claims.map((p) => p.claim_id).join(", ")}
        </p>
      )}
    </div>
  );
}

function Review({ runId, onDone }) {
  const [cp, setCp] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [showOutput, setShowOutput] = useState(false);
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState("");

  useEffect(() => {
    api("GET", `/runs/${runId}/checkpoint`)
      .then((o) => { setCp(o.data); setEdited(JSON.stringify(o.data.output, null, 2)); })
      .catch((x) => setErr(String(x.message || x)));
  }, [runId]);

  const decide = async (action) => {
    setBusy(true); setErr("");
    try {
      const body = { action, comment };
      if (action === "edit") body.edited_output = JSON.parse(edited);
      if (action === "rerun" && !comment.trim()) throw new Error("Say what the agent should change before rerunning.");
      await api("POST", `/runs/${runId}/checkpoint`, body);
      onDone();
    } catch (x) { setErr(String(x.message || x)); setBusy(false); }
  };

  if (err && !cp) return <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{err}</p>;
  if (!cp) return <Loader2 className="h-5 w-5 animate-spin text-slate-400" />;
  return (
    <section className={`${card} border-l-4 border-amber-400 p-5`}>
      <div className="flex items-center gap-2 text-amber-800">
        <PauseCircle className="h-5 w-5" />
        <h3 className="text-base font-semibold">Your review: {agentName(cp.checkpoint)}{cp.hypothesis_id ? ` · hypothesis ${cp.hypothesis_id}` : ""}</h3>
      </div>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-700">
        {(cp.summary || []).map((s, i) => <li key={i}>{s}</li>)}
      </ul>

      <div className="mt-4 rounded-lg bg-slate-50 p-3">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
          <ShieldCheck className="h-4 w-4 text-slate-600" /> Citation check (every claim against its source)
        </div>
        {Object.keys(cp.citations || {}).length === 0
          ? <p className="mt-2 text-sm text-slate-500">No sourced claims to check at this step.</p>
          : Object.entries(cp.citations).map(([k, r]) => <Ledger key={k} name={agentName(k.split(":")[0]) + (k.split(":")[1] ? ` · ${k.split(":")[1]}` : "")} report={r} />)}
      </div>

      <button className="mt-4 flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900" onClick={() => setShowOutput(!showOutput)}>
        {showOutput ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />} Agent output
      </button>
      {showOutput && !editing && (
        <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-slate-900 p-3 text-xs text-slate-100">{JSON.stringify(cp.output, null, 2)}</pre>
      )}
      {editing && (
        <textarea className="mt-2 h-80 w-full rounded-md border border-slate-300 p-2 font-mono text-xs" value={edited} onChange={(e) => setEdited(e.target.value)} />
      )}

      <label className="mt-4 block text-sm font-medium text-slate-700">Comment for the agent (needed to rerun)
        <textarea className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" rows={2} value={comment}
          placeholder="e.g. Restrict the segment to former smokers with eosinophils ≥300" onChange={(e) => setComment(e.target.value)} />
      </label>
      {err && <p className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">{err}</p>}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className={primary} disabled={busy} onClick={() => decide("approve")}><CheckCircle2 className="h-4 w-4" /> Approve</button>
        <button className={secondary} disabled={busy} onClick={() => decide("approve_all")}>Approve the rest for this agent</button>
        <button className={secondary} disabled={busy} onClick={() => decide("rerun")}><RefreshCw className="h-4 w-4" /> Rerun with comment</button>
        {!editing
          ? <button className={secondary} disabled={busy} onClick={() => { setEditing(true); setShowOutput(true); }}>Edit output</button>
          : <button className={secondary} disabled={busy} onClick={() => decide("edit")}>Save edit and continue</button>}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- results
async function download(runId, name) {
  const out = await api("GET", `/runs/${runId}/files/${name}`);
  const blob = out.base64
    ? new Blob([Uint8Array.from(atob(out.base64), (c) => c.charCodeAt(0))], { type: out.contentType })
    : new Blob([out.text ?? JSON.stringify(out.data, null, 2)], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}

function Results({ runId }) {
  const [report, setReport] = useState("");
  const [files, setFiles] = useState([]);
  useEffect(() => {
    api("GET", `/runs/${runId}/report`).then((o) => setReport(o.text || "")).catch(() => {});
    api("GET", `/runs/${runId}/files`).then((o) => setFiles(o.data || [])).catch(() => {});
  }, [runId]);
  return (
    <>
      <Section title="Downloads">
        {files.length === 0 ? <p className="text-sm text-slate-500">No files yet.</p> : (
          <div className="flex flex-wrap gap-2">
            {files.map((f) => (
              <button key={f.name} className={secondary} onClick={() => download(runId, f.name)}>
                <Download className="h-4 w-4" /> {f.name}
              </button>
            ))}
          </div>
        )}
      </Section>
      <Section title="Report">
        {report
          ? <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap font-sans text-sm leading-relaxed text-slate-800">{report}</pre>
          : <p className="text-sm text-slate-500">The report appears when the run finishes.</p>}
      </Section>
    </>
  );
}

// ---------------------------------------------------------------- run view
function RunView({ runId }) {
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);

  const load = useCallback(() => {
    api("GET", `/runs/${runId}`).then((o) => { setRun(o.data); setErr(""); }).catch((x) => setErr(String(x.message || x)));
  }, [runId]);

  useEffect(() => { setRun(null); load(); }, [runId, load]);
  useEffect(() => {
    if (run && run.status !== "running") return;
    const t = setTimeout(() => { load(); setTick(tick + 1); }, 2500);
    return () => clearTimeout(t);
  }, [run, tick, load]);

  if (err && !run) return <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{err}</p>;
  if (!run) return <Loader2 className="h-6 w-6 animate-spin text-slate-400" />;

  const s = run.settings || {};
  const flagged = Object.values(run.citations || {}).reduce((n, c) => n + (c.flagged || 0), 0);
  const checks = Object.values(run.citations || {}).reduce((n, c) => n + (c.checked_pairs || 0), 0);
  return (
    <div className="space-y-5">
      <div className={`${card} flex flex-wrap items-center justify-between gap-3 p-5`}>
        <div>
          <h2 className="text-xl font-semibold text-slate-900">{s.disease || "Analysis"} · run {run.run_id}</h2>
          <p className="mt-1 text-sm text-slate-500">
            Evidence cutoff {s.as_of || "today"} · {s.mode === "single_shot" ? "single shot" : "guided review"} · {s.audience || "generalist"} · {s.llm === "stub" ? "test mode" : "live models"}
          </p>
        </div>
        <StatusPill status={run.status} />
      </div>
      {run.error && <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{run.error}</p>}

      {run.status === "paused" && <Review key={`${run.run_id}-${run.step_count}`} runId={run.run_id} onDone={load} />}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Section title="Hypotheses">
          {run.hypotheses?.length ? (
            <ul className="space-y-2 text-sm">
              {run.hypotheses.map((h) => {
                const v = run.verdicts?.[h.id];
                const killed = run.killed?.some((k) => k.hypothesis_id === h.id);
                return (
                  <li key={h.id} className="flex items-start justify-between gap-2">
                    <span><span className="font-mono text-slate-500">{h.id}</span> {h.mechanism}{run.current_hypothesis === h.id && run.status === "running" ? " (in progress)" : ""}</span>
                    {(v || killed) && (
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${killed || v === "kill" ? "bg-red-50 text-red-700" : v === "survives" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>
                        {killed ? "killed" : v}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : <p className="text-sm text-slate-500">Ranked after A2 runs.</p>}
        </Section>
        <Section title="Citation checks">
          <p className="text-3xl font-semibold text-slate-900">{checks}</p>
          <p className="text-sm text-slate-500">claim-to-source checks so far</p>
          <p className={`mt-2 text-sm ${flagged ? "text-red-700" : "text-emerald-700"}`}>{flagged} flagged (missing, too late or unsupported)</p>
        </Section>
        <Section title="Cost">
          <p className="text-3xl font-semibold text-slate-900">${(run.cost?.total_usd ?? 0).toFixed(4)}</p>
          <ul className="mt-2 space-y-0.5 text-xs text-slate-500">
            {Object.entries(run.cost?.per_agent || {}).map(([a, u]) => (
              <li key={a}>{agentName(a)}: {u.calls} call(s){u.usd != null ? ` · $${u.usd.toFixed(4)}` : ""}</li>
            ))}
          </ul>
        </Section>
      </div>

      <Section title={`Steps (${run.step_count})`}>
        <ol className="max-h-80 space-y-1 overflow-auto text-sm">
          {[...(run.steps || [])].reverse().map((st, i) => (
            <li key={i} className={`flex gap-2 ${st.node?.startsWith("a7c_") ? "rounded bg-slate-50 px-1" : ""}`}>
              {st.node?.startsWith("a7c_") ? <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" /> : <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />}
              <span className="font-mono text-xs text-slate-500">{st.node}{st.hid ? `·${st.hid}` : ""}</span>
              <span className="text-slate-700">{st.detail}</span>
            </li>
          ))}
        </ol>
      </Section>

      {run.status === "done" && <Results runId={run.run_id} />}
    </div>
  );
}

// ---------------------------------------------------------------- sign in
// Our own sign-in screen. (redirectToLogin() sends visitors to /login on this site, which
// for a CLI-deployed app is this same page, so it loops; loginWithProvider goes through
// Base44's auth service instead.)
function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const withPassword = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await base44.auth.loginViaEmailPassword(email, password);
      const token = res?.access_token || res?.data?.access_token;
      if (token) base44.auth.setToken(token, true);
      window.location.reload();
    } catch (x) { setErr("Sign-in failed. Check your email and password, or use Google."); setBusy(false); }
  };

  const field = "mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none";
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <div className={`${card} w-full max-w-sm p-8`}>
        <div className="flex items-center gap-3">
          <FlaskConical className="h-7 w-7 text-teal-600" />
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Frontier Hypothesis Engine</h1>
            <p className="text-xs text-slate-500">UPMC Enterprises · sign in to continue</p>
          </div>
        </div>
        <button className={`${primary} mt-6 w-full`} onClick={() => base44.auth.loginWithProvider("google", window.location.origin + "/")}>
          Continue with Google
        </button>
        <div className="my-5 flex items-center gap-3 text-xs text-slate-400"><span className="h-px flex-1 bg-slate-200" />or<span className="h-px flex-1 bg-slate-200" /></div>
        <form onSubmit={withPassword} className="space-y-3">
          <label className="block text-sm font-medium text-slate-700">Email
            <input type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="block text-sm font-medium text-slate-700">Password
            <input type="password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {err && <p className="rounded-md bg-red-50 p-2 text-sm text-red-700">{err}</p>}
          <button className={`${secondary} w-full`} disabled={busy}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Sign in</button>
        </form>
        <p className="mt-5 flex items-center gap-1.5 text-xs text-slate-500"><ShieldCheck className="h-3.5 w-3.5" /> Only signed-in users can start or review analyses.</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shell
export default function App() {
  const [user, setUser] = useState(null);
  const [authState, setAuthState] = useState("checking"); // checking | in | out
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    base44.auth.me()
      .then((u) => { setUser(u); setAuthState("in"); })
      .catch(() => setAuthState("out"));
  }, []);

  const loadRuns = useCallback(() => {
    api("GET", "/runs").then((o) => { setRuns(o.data || []); setErr(""); }).catch((x) => setErr(String(x.message || x)));
  }, []);
  useEffect(() => { if (user) loadRuns(); }, [user, loadRuns, selected]);

  if (authState === "checking") return <div className="flex h-screen items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-slate-400" /></div>;
  if (authState === "out") return <SignIn />;

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="bg-slate-900 text-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <FlaskConical className="h-6 w-6 text-teal-300" />
            <div>
              <h1 className="text-lg font-semibold">Frontier Hypothesis Engine</h1>
              <p className="text-xs text-slate-300">Evidence-checked biotech investment theses · UPMC Enterprises</p>
            </div>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-300">
            <span>{user.email}</span>
            <button className="rounded p-1 hover:bg-slate-800" title="Log out" onClick={() => base44.auth.logout(window.location.origin + "/")}><LogOut className="h-4 w-4" /></button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[16rem_1fr]">
        <aside className="space-y-3">
          <button className={`${primary} w-full`} onClick={() => setSelected(null)}><Plus className="h-4 w-4" /> New analysis</button>
          <div className={`${card} p-2`}>
            <p className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Runs</p>
            {err && <p className="px-2 py-1 text-xs text-red-700">{err}</p>}
            {runs.length === 0 && !err && <p className="px-2 py-1 text-sm text-slate-500">No runs yet.</p>}
            {runs.map((r) => (
              <button key={r.run_id} onClick={() => setSelected(r.run_id)}
                className={`flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm hover:bg-slate-50 ${selected === r.run_id ? "bg-slate-100" : ""}`}>
                <span className="flex items-center gap-2"><FileText className="h-4 w-4 text-slate-400" />{r.settings?.disease || "Run"} · {r.run_id}</span>
                <span className={`h-2 w-2 rounded-full ${r.status === "paused" ? "bg-amber-400" : r.status === "done" ? "bg-emerald-500" : r.status === "error" ? "bg-red-500" : "bg-blue-500"}`} />
              </button>
            ))}
          </div>
        </aside>
        <main>{selected ? <RunView runId={selected} /> : <NewRun onStarted={setSelected} />}</main>
      </div>
    </div>
  );
}
