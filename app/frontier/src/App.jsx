import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { base44 } from "@/api/base44Client";
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, ChevronDown, ChevronRight, Clock, Download,
  FileSpreadsheet, FileText, FlaskConical, Layers, Loader2, LogOut, PauseCircle, Play, Plus,
  RefreshCw, ShieldCheck, Target, XCircle,
} from "lucide-react";

// ---------------------------------------------------------------- engine calls
// Every call goes through the frontierApi backend function, which holds the engine's
// token server-side and checks the user is signed in. The browser never sees the token.
async function api(method, path, body) {
  const res = await base44.functions.invoke("frontierApi", { method, path, body });
  const out = res?.data ?? res;
  if (out?.error) throw new Error(out.error);
  if (out?.status >= 400) throw new Error(out?.data?.detail || out?.text || `Engine error ${out.status}`);
  return out;
}

const AGENTS = {
  a1_disease: "Why now", a2_mechanism: "Mechanisms", a3_profile: "Product profile",
  a4_inactive: "Shelved & repurposable assets", a5_active: "Competitor landscape",
  a6_redteam: "Red team", a7_judge: "Judge", a7_citations: "Citation check",
};
const agentName = (a) => AGENTS[a] || a;

// Consistent status colours everywhere: green good, amber caution, red stop.
const TONE = {
  good: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warn: "bg-amber-50 text-amber-800 ring-amber-200",
  bad: "bg-rose-50 text-rose-700 ring-rose-200",
  info: "bg-sky-50 text-sky-700 ring-sky-200",
  muted: "bg-slate-100 text-slate-600 ring-slate-200",
};
const Pill = ({ tone = "muted", children, className = "" }) => (
  <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${TONE[tone]} ${className}`}>{children}</span>
);

const STATUS = {
  running: { label: "Running", icon: Loader2, tone: "info", spin: true },
  paused: { label: "Waiting for your review", icon: PauseCircle, tone: "warn" },
  done: { label: "Complete", icon: CheckCircle2, tone: "good" },
  error: { label: "Stopped with an error", icon: XCircle, tone: "bad" },
  stopped: { label: "Stopped", icon: AlertTriangle, tone: "muted" },
};
function StatusPill({ status }) {
  const s = STATUS[status] || STATUS.stopped;
  const Icon = s.icon;
  return <Pill tone={s.tone} className="px-3 py-1 text-sm"><Icon className={`h-4 w-4 ${s.spin ? "animate-spin" : ""}`} />{s.label}</Pill>;
}

const btn = "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-50 disabled:pointer-events-none";
const primary = `${btn} bg-slate-900 text-white hover:bg-slate-700`;
const secondary = `${btn} bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50`;
const card = "rounded-2xl bg-white ring-1 ring-slate-200/80 shadow-sm";

function Section({ title, icon: Icon, children, right, className = "" }) {
  return (
    <section className={`${card} p-5 ${className}`}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">{Icon && <Icon className="h-4 w-4 text-teal-600" />}{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- small charts (SVG/CSS, no libraries)
function Ring({ value, total, size = 88 }) {
  const pct = total ? value / total : 0;
  const r = 34, c = 2 * Math.PI * r;
  const tone = pct >= 0.8 ? "#059669" : pct >= 0.5 ? "#d97706" : "#e11d48";
  return (
    <svg width={size} height={size} viewBox="0 0 88 88" className="shrink-0">
      <circle cx="44" cy="44" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
      <circle cx="44" cy="44" r={r} fill="none" stroke={tone} strokeWidth="10" strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`} transform="rotate(-90 44 44)" />
      <text x="44" y="49" textAnchor="middle" className="fill-slate-900 text-[17px] font-semibold">{total ? Math.round(pct * 100) : 0}%</text>
    </svg>
  );
}

function StackedBar({ parts, total }) {
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
      {parts.map((p, i) => p.value > 0 && (
        <div key={i} className={p.className} style={{ width: `${(p.value / Math.max(total, 1)) * 100}%` }} title={`${p.label}: ${p.value}`} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- pipeline tracker
const STAGES = [
  { key: "a1", label: "Why now", done: ["a1_commit"] },
  { key: "a2", label: "Mechanisms", done: ["a2_commit"] },
  { key: "a3", label: "Product profile", done: ["a3_commit"] },
  { key: "a45", label: "Competitors", done: ["a4_commit", "a5_commit"], prefixes: ["a4", "a5"] },
  { key: "a6", label: "Red team", done: ["a6_commit"] },
  { key: "a7", label: "Judge", done: ["a7_judge", "a6_route"], prefixes: ["a7_judge", "a6_route"] },
  { key: "report", label: "Report", done: ["report"] },
];

function Pipeline({ run }) {
  const nodes = new Set((run.steps || []).map((s) => s.node));
  const last = (run.steps || [])[ (run.steps || []).length - 1 ]?.node || "";
  const active = run.status === "running" || run.status === "paused";
  const currentIdx = STAGES.findIndex((st) =>
    (st.prefixes || [st.key]).some((p) => last.startsWith(p)) || (st.key === "a1" && last === "setup"));
  return (
    <ol className="flex flex-wrap items-center gap-y-3">
      {STAGES.map((st, i) => {
        const done = run.status === "done" || st.done.some((n) => nodes.has(n));
        const current = active && i === currentIdx;
        const dot = current ? "bg-teal-600 text-white ring-4 ring-teal-100"
          : done ? "bg-emerald-500 text-white" : "bg-slate-200 text-slate-500";
        return (
          <li key={st.key} className="flex items-center">
            <div className="flex items-center gap-2">
              <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${dot}`}>
                {done && !current ? <CheckCircle2 className="h-4 w-4" /> : current && run.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : i + 1}
              </span>
              <span className={`text-sm ${current ? "font-semibold text-slate-900" : done ? "text-slate-700" : "text-slate-400"}`}>{st.label}</span>
            </div>
            {i < STAGES.length - 1 && <span className={`mx-3 h-px w-6 sm:w-10 ${done ? "bg-emerald-400" : "bg-slate-200"}`} />}
          </li>
        );
      })}
    </ol>
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

  const field = "mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-100";
  const label = "block text-sm font-medium text-slate-700";
  const steps = [
    ["Why now", "Shifts in endpoints, segments and trial tools"],
    ["Mechanisms", "Ranked hypotheses with evidence tiers"],
    ["Winning product", "Must-haves and the gap at launch"],
    ["Competitors", "Every active and shelved program"],
    ["Red team + judge", "What could kill each hypothesis"],
    ["Citation check", "Every claim checked against its source"],
  ];
  return (
    <div className="space-y-6">
      <div className={`${card} overflow-hidden`}>
        <div className="bg-gradient-to-r from-slate-900 via-slate-800 to-teal-900 px-6 py-6 text-white">
          <h2 className="text-2xl font-semibold">New analysis</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-300">Turn one disease area into 2 to 3 evidence-checked pursuits an investor can act on this year.</p>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {steps.map(([t, d], i) => (
              <div key={t} className="rounded-xl bg-white/10 p-3">
                <p className="text-xs font-semibold text-teal-200">{i + 1}. {t}</p>
                <p className="mt-1 text-xs text-slate-300">{d}</p>
              </div>
            ))}
          </div>
        </div>
        <form onSubmit={start} className="p-6">
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <label className={label}>Disease area<input className={field} value={f.disease} onChange={set("disease")} /></label>
            <label className={label}>Evidence cutoff
              <input type="date" className={field} value={f.as_of} onChange={set("as_of")} disabled={f.llm === "stub"} />
              <span className="mt-1 block text-xs text-slate-500">
                {f.llm === "stub" ? "Test mode uses sample evidence up to 2020, so the cutoff is fixed at 2021-01-01." : "Nothing published after this date is used (backtests use an earlier date)."}
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
          {err && <p className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</p>}
          <div className="mt-6 flex items-center justify-between">
            <p className="flex items-center gap-1.5 text-xs text-slate-500"><ShieldCheck className="h-4 w-4" /> Public sources only. Every claim is cited and checked.</p>
            <button className={primary} disabled={busy}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Start analysis</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- citations
const VERDICT = { yes: "good", partly: "warn", no: "bad", not_found: "bad", after_cutoff: "bad" };

function Ledger({ name, report }) {
  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-slate-800">{name}</span>
        <Pill tone="good">{report.kept} kept</Pill><Pill tone="warn">{report.downgraded} downgraded</Pill><Pill tone="bad">{report.flagged} flagged</Pill>
      </div>
      {report.entries?.length > 0 && (
        <div className="max-h-56 overflow-auto rounded-lg ring-1 ring-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-50 text-slate-500">
              <tr><th className="px-3 py-2">Claim</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Dated</th><th className="px-3 py-2">Supports the claim?</th></tr>
            </thead>
            <tbody>
              {report.entries.map((e, i) => (
                <tr key={i} className="border-t border-slate-100">
                  <td className="px-3 py-1.5 font-mono text-slate-600">{e.claim_id}</td>
                  <td className="px-3 py-1.5 font-mono text-slate-600">{e.source_id}</td>
                  <td className="px-3 py-1.5 text-slate-600">{e.source_date || "—"}</td>
                  <td className="px-3 py-1.5"><Pill tone={VERDICT[e.supports]}>{e.supports.replace("_", " ")}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.invalid_claims?.length > 0 && (
        <p className="mt-1 text-xs text-rose-700">{report.invalid_claims.length} claim(s) break the evidence rules: {report.invalid_claims.map((p) => p.claim_id).join(", ")}</p>
      )}
    </div>
  );
}

function EvidenceChart({ citations }) {
  const byAgent = {};
  for (const [k, c] of Object.entries(citations || {})) {
    const a = k.split(":")[0];
    const t = byAgent[a] || (byAgent[a] = { kept: 0, downgraded: 0, flagged: 0, checked: 0 });
    t.kept += c.kept || 0; t.downgraded += c.downgraded || 0; t.flagged += c.flagged || 0; t.checked += c.checked_pairs || 0;
  }
  const rows = Object.entries(byAgent);
  if (!rows.length) return <p className="text-sm text-slate-500">Citation checks appear as each agent finishes.</p>;
  return (
    <div className="space-y-4">
      {rows.map(([a, t]) => {
        const total = t.kept + t.downgraded + t.flagged;
        return (
          <div key={a}>
            <div className="mb-1.5 flex justify-between text-sm"><span className="text-slate-700">{agentName(a)}</span><span className="text-slate-500">{t.checked} checks</span></div>
            <StackedBar total={total} parts={[
              { value: t.kept, label: "kept", className: "bg-emerald-500" },
              { value: t.downgraded, label: "downgraded", className: "bg-amber-400" },
              { value: t.flagged, label: "flagged", className: "bg-rose-500" },
            ]} />
          </div>
        );
      })}
      <div className="flex gap-4 pt-1 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-500" />Supported</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-400" />Partly supported</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-500" />Missing, too late or unsupported</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- hypotheses
const LEVEL = { gold: "bg-amber-100 text-amber-900 ring-amber-300", silver: "bg-slate-100 text-slate-700 ring-slate-300", bronze: "bg-orange-100 text-orange-900 ring-orange-300" };
const verdictTone = (v, killed) => (killed || v === "kill" ? "bad" : v === "survives" ? "good" : v ? "warn" : "muted");

function HypothesisCard({ h, killed, current }) {
  const v = killed ? "killed" : h.verdict || (current ? "in progress" : "queued");
  return (
    <div className={`${card} flex flex-col p-5 ${current ? "ring-2 ring-teal-400" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <span className="font-mono text-xs text-slate-400">{h.id}</span>
        <Pill tone={verdictTone(h.verdict, killed)}>{v}</Pill>
      </div>
      <h4 className="mt-2 text-base font-semibold leading-snug text-slate-900">{h.mechanism}</h4>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {h.validation_level && <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ${LEVEL[h.validation_level] || LEVEL.silver}`}>{h.validation_level}</span>}
        {h.evidence_tier != null && <Pill tone="info">Evidence tier {h.evidence_tier}</Pill>}
        {h.direction && <Pill>{h.direction}</Pill>}
        {h.modality && <Pill>{h.modality}</Pill>}
      </div>
      {h.top_risk && <p className="mt-4 text-sm text-slate-700"><span className="font-medium text-rose-700">Main risk: </span>{h.top_risk}</p>}
      {h.cheapest_test && <p className="mt-2 text-sm text-slate-700"><span className="font-medium text-teal-700">Cheapest test: </span>{h.cheapest_test}</p>}
      {h.judge_decision && <p className="mt-auto pt-3 text-xs text-slate-500">Judge: {h.judge_decision}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- review
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

  if (err && !cp) return <p className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</p>;
  if (!cp) return <div className={`${card} p-5`}><Loader2 className="h-5 w-5 animate-spin text-slate-400" /></div>;
  return (
    <section className={`${card} overflow-hidden ring-2 ring-amber-300`}>
      <div className="flex items-center gap-2 bg-amber-50 px-5 py-3 text-amber-900">
        <PauseCircle className="h-5 w-5" />
        <h3 className="text-base font-semibold">Your review · {agentName(cp.checkpoint)}{cp.hypothesis_id ? ` · hypothesis ${cp.hypothesis_id}` : ""}</h3>
      </div>
      <div className="p-5">
        <ul className="space-y-1.5 text-sm text-slate-700">
          {(cp.summary || []).map((s, i) => <li key={i} className="flex gap-2"><ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />{s}</li>)}
        </ul>
        <div className="mt-4 rounded-xl bg-slate-50 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><ShieldCheck className="h-4 w-4 text-teal-600" /> Citation check</div>
          {Object.keys(cp.citations || {}).length === 0
            ? <p className="mt-2 text-sm text-slate-500">No sourced claims to check at this step.</p>
            : Object.entries(cp.citations).map(([k, r]) => <Ledger key={k} name={agentName(k.split(":")[0]) + (k.split(":")[1] ? ` · ${k.split(":")[1]}` : "")} report={r} />)}
        </div>
        <button className="mt-4 flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900" onClick={() => setShowOutput(!showOutput)}>
          {showOutput ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />} Full agent output
        </button>
        {showOutput && !editing && <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{JSON.stringify(cp.output, null, 2)}</pre>}
        {editing && <textarea className="mt-2 h-80 w-full rounded-lg border border-slate-300 p-2 font-mono text-xs" value={edited} onChange={(e) => setEdited(e.target.value)} />}
        <label className="mt-4 block text-sm font-medium text-slate-700">Comment for the agent (needed to rerun)
          <textarea className="mt-1.5 w-full rounded-lg border border-slate-300 p-2 text-sm" rows={2} value={comment}
            placeholder="e.g. Restrict the segment to former smokers with eosinophils ≥300" onChange={(e) => setComment(e.target.value)} />
        </label>
        {err && <p className="mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</p>}
        <div className="mt-4 flex flex-wrap gap-2">
          <button className={primary} disabled={busy} onClick={() => decide("approve")}><CheckCircle2 className="h-4 w-4" /> Approve</button>
          <button className={secondary} disabled={busy} onClick={() => decide("approve_all")}>Approve the rest for this agent</button>
          <button className={secondary} disabled={busy} onClick={() => decide("rerun")}><RefreshCw className="h-4 w-4" /> Rerun with comment</button>
          {!editing
            ? <button className={secondary} disabled={busy} onClick={() => { setEditing(true); setShowOutput(true); }}>Edit output</button>
            : <button className={secondary} disabled={busy} onClick={() => decide("edit")}>Save edit and continue</button>}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- report + files
async function download(runId, name) {
  const out = await api("GET", `/runs/${runId}/files/${name}`);
  const blob = out.base64
    ? new Blob([Uint8Array.from(atob(out.base64), (c) => c.charCodeAt(0))], { type: out.contentType })
    : new Blob([out.text ?? JSON.stringify(out.data, null, 2)], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  URL.revokeObjectURL(a.href);
}

const PRIMARY_FILES = [
  { match: (n) => /memo/i.test(n), label: "Investor memo", icon: FileText },
  { match: (n) => n === "report.md", label: "Full report", icon: FileText },
  { match: (n) => n.endsWith(".xlsx"), label: "Analysis workbook (Excel)", icon: FileSpreadsheet },
  { match: (n) => n === "result.json", label: "Structured results (JSON)", icon: Layers },
];

function Downloads({ runId }) {
  const [files, setFiles] = useState([]);
  useEffect(() => { api("GET", `/runs/${runId}/files`).then((o) => setFiles(o.data || [])).catch(() => {}); }, [runId]);
  const primaryFiles = files.map((f) => ({ f, kind: PRIMARY_FILES.find((p) => p.match(f.name)) })).filter((x) => x.kind);
  const technical = files.filter((f) => !PRIMARY_FILES.some((p) => p.match(f.name)));
  return (
    <Section title="Downloads" icon={Download}>
      {files.length === 0 ? <p className="text-sm text-slate-500">Files appear when the run finishes.</p> : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {primaryFiles.map(({ f, kind }) => {
              const Icon = kind.icon;
              return (
                <button key={f.name} onClick={() => download(runId, f.name)} className="flex items-center gap-3 rounded-xl p-3 text-left ring-1 ring-slate-200 transition hover:bg-slate-50 hover:ring-teal-300">
                  <span className="rounded-lg bg-teal-50 p-2 text-teal-700"><Icon className="h-5 w-5" /></span>
                  <span><span className="block text-sm font-medium text-slate-900">{kind.label}</span><span className="text-xs text-slate-500">{f.name}</span></span>
                </button>
              );
            })}
          </div>
          {technical.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer text-slate-500 hover:text-slate-800">Technical files ({technical.length})</summary>
              <div className="mt-2 flex flex-wrap gap-2">
                {technical.map((f) => <button key={f.name} className="rounded-lg px-2.5 py-1 text-xs ring-1 ring-slate-200 hover:bg-slate-50" onClick={() => download(runId, f.name)}>{f.name}</button>)}
              </div>
            </details>
          )}
        </>
      )}
    </Section>
  );
}

function Report({ runId, done }) {
  const [report, setReport] = useState("");
  useEffect(() => { if (done) api("GET", `/runs/${runId}/report`).then((o) => setReport(o.text || "")).catch(() => {}); }, [runId, done]);
  return (
    <>
      <Downloads runId={runId} />
      <Section title="Report" icon={FileText}>
        {report ? (
          <article className="prose prose-slate max-w-none prose-headings:font-semibold prose-h1:text-2xl prose-h2:mt-8 prose-h2:border-b prose-h2:border-slate-200 prose-h2:pb-2 prose-table:text-sm prose-th:bg-slate-50 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-td:align-top">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
          </article>
        ) : <p className="text-sm text-slate-500">The report appears when the run finishes.</p>}
      </Section>
    </>
  );
}

// ---------------------------------------------------------------- run view
const TABS = [
  { key: "overview", label: "Overview", icon: BarChart3 },
  { key: "hypotheses", label: "Hypotheses", icon: Target },
  { key: "evidence", label: "Evidence checks", icon: ShieldCheck },
  { key: "report", label: "Report", icon: FileText },
  { key: "activity", label: "Activity", icon: Activity },
];

function Stat({ label, value, sub, children }) {
  return (
    <div className={`${card} flex items-center gap-4 p-5`}>
      {children}
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
        {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
      </div>
    </div>
  );
}

function RunView({ runId }) {
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  const [tab, setTab] = useState("overview");

  const load = useCallback(() => {
    api("GET", `/runs/${runId}`).then((o) => { setRun(o.data); setErr(""); }).catch((x) => setErr(String(x.message || x)));
  }, [runId]);
  useEffect(() => { setRun(null); setTab("overview"); load(); }, [runId, load]);
  useEffect(() => {
    if (run && run.status !== "running") return;
    const t = setTimeout(() => { load(); setTick(tick + 1); }, 2500);
    return () => clearTimeout(t);
  }, [run, tick, load]);

  const stats = useMemo(() => {
    if (!run) return null;
    const c = Object.values(run.citations || {});
    const kept = c.reduce((n, x) => n + (x.kept || 0), 0);
    const total = c.reduce((n, x) => n + (x.kept || 0) + (x.downgraded || 0) + (x.flagged || 0), 0);
    const flagged = c.reduce((n, x) => n + (x.flagged || 0), 0);
    const hs = run.hypotheses || [];
    const killed = new Set((run.killed || []).map((k) => k.hypothesis_id));
    const survived = hs.filter((h) => h.verdict === "survives" && !killed.has(h.id)).length;
    return { kept, total, flagged, hs, killed, survived };
  }, [run]);

  if (err && !run) return <p className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</p>;
  if (!run) return <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-slate-400" /></div>;

  const s = run.settings || {};
  return (
    <div className="space-y-5">
      <div className={`${card} p-6`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold text-slate-900">{s.disease || "Analysis"} <span className="font-normal text-slate-400">· run {run.run_id}</span></h2>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill>Cutoff {s.as_of || "today"}</Pill>
              <Pill>{s.mode === "single_shot" ? "Single shot" : "Guided review"}</Pill>
              <Pill>{s.audience === "specialist" ? "Clinical specialist" : "Generalist investor"}</Pill>
              <Pill tone={s.llm === "stub" ? "warn" : "info"}>{s.llm === "stub" ? "Test mode" : `Live · ${s.profile === "final" ? "final" : "fast"} models`}</Pill>
            </div>
          </div>
          <StatusPill status={run.status} />
        </div>
        <div className="mt-6"><Pipeline run={run} /></div>
      </div>

      {run.error && <p className="rounded-lg bg-rose-50 p-4 text-sm text-rose-700"><span className="font-semibold">The run stopped: </span>{run.error}</p>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Hypotheses" value={stats.hs.length || "—"} sub={stats.hs.length ? `${stats.survived} survived · ${stats.killed.size} killed` : "Ranked after Mechanisms"} />
        <Stat label="Citation integrity" value={`${stats.kept}/${stats.total}`} sub="claim-source pairs supported"><Ring value={stats.kept} total={stats.total} /></Stat>
        <Stat label="Flagged citations" value={stats.flagged} sub="missing, too late or unsupported" />
        <Stat label="Model cost" value={`$${(run.cost?.total_usd ?? 0).toFixed(4)}`} sub={`${run.step_count} steps`} />
      </div>

      {run.status === "paused" && <Review key={`${run.run_id}-${run.step_count}`} runId={run.run_id} onDone={load} />}

      <nav className="flex gap-1 overflow-x-auto rounded-xl bg-white p-1 ring-1 ring-slate-200">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition ${tab === t.key ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>
              <Icon className="h-4 w-4" />{t.label}
            </button>
          );
        })}
      </nav>

      {tab === "overview" && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
          <Section title="Hypotheses at a glance" icon={Target} className="lg:col-span-3">
            {stats.hs.length ? (
              <ul className="divide-y divide-slate-100">
                {stats.hs.map((h) => (
                  <li key={h.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-900">{h.mechanism}</p>
                      <p className="text-xs text-slate-500">{h.id} · {h.validation_level || "—"} · tier {h.evidence_tier ?? "—"}</p>
                    </div>
                    <Pill tone={verdictTone(h.verdict, stats.killed.has(h.id))}>{stats.killed.has(h.id) ? "killed" : h.verdict || (run.current_hypothesis === h.id ? "in progress" : "queued")}</Pill>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-slate-500">Ranked after the Mechanisms agent runs.</p>}
          </Section>
          <Section title="Evidence checks by agent" icon={ShieldCheck} className="lg:col-span-2"><EvidenceChart citations={run.citations} /></Section>
        </div>
      )}
      {tab === "hypotheses" && (
        stats.hs.length
          ? <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">{stats.hs.map((h) => <HypothesisCard key={h.id} h={h} killed={stats.killed.has(h.id)} current={run.current_hypothesis === h.id && run.status !== "done"} />)}</div>
          : <Section title="Hypotheses" icon={Target}><p className="text-sm text-slate-500">Ranked after the Mechanisms agent runs.</p></Section>
      )}
      {tab === "evidence" && <Section title="Evidence checks by agent" icon={ShieldCheck}><EvidenceChart citations={run.citations} /></Section>}
      {tab === "report" && <Report runId={run.run_id} done={run.status === "done"} />}
      {tab === "activity" && (
        <Section title={`Activity (${run.step_count} steps)`} icon={Activity}>
          <ol className="max-h-[32rem] space-y-1 overflow-auto text-sm">
            {[...(run.steps || [])].reverse().map((st, i) => (
              <li key={i} className={`flex gap-3 rounded-lg px-2 py-1.5 ${st.node?.startsWith("a7c_") ? "bg-teal-50/60" : ""}`}>
                {st.node?.startsWith("a7c_") ? <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal-600" /> : <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />}
                <span className="w-36 shrink-0 font-mono text-xs leading-5 text-slate-500">{st.node}{st.hid ? ` · ${st.hid}` : ""}</span>
                <span className="text-slate-700">{st.detail}</span>
              </li>
            ))}
          </ol>
        </Section>
      )}
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
    } catch { setErr("Sign-in failed. Check your email and password, or use Google."); setBusy(false); }
  };

  const field = "mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-100";
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-teal-900 px-4">
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-2xl">
        <div className="flex items-center gap-3">
          <span className="rounded-xl bg-teal-50 p-2.5"><FlaskConical className="h-6 w-6 text-teal-600" /></span>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Ctrl+Alt+Cure</h1>
            <p className="text-xs text-slate-500">UPMC Enterprises · sign in to continue</p>
          </div>
        </div>
        <button className={`${primary} mt-6 w-full`} onClick={() => base44.auth.loginWithProvider("google", window.location.origin + "/")}>Continue with Google</button>
        <div className="my-5 flex items-center gap-3 text-xs text-slate-400"><span className="h-px flex-1 bg-slate-200" />or<span className="h-px flex-1 bg-slate-200" /></div>
        <form onSubmit={withPassword} className="space-y-3">
          <label className="block text-sm font-medium text-slate-700">Email<input type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label className="block text-sm font-medium text-slate-700">Password<input type="password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {err && <p className="rounded-lg bg-rose-50 p-2 text-sm text-rose-700">{err}</p>}
          <button className={`${secondary} w-full`} disabled={busy}>{busy && <Loader2 className="h-4 w-4 animate-spin" />} Sign in</button>
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
    document.title = "Ctrl+Alt+Cure · Evidence-checked biotech investment theses";
    base44.auth.me().then((u) => { setUser(u); setAuthState("in"); }).catch(() => setAuthState("out"));
  }, []);
  const loadRuns = useCallback(() => {
    api("GET", "/runs").then((o) => { setRuns(o.data || []); setErr(""); }).catch((x) => setErr(String(x.message || x)));
  }, []);
  useEffect(() => { if (user) loadRuns(); }, [user, loadRuns, selected]);

  if (authState === "checking") return <div className="flex h-screen items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-slate-400" /></div>;
  if (authState === "out") return <SignIn />;

  const dot = { paused: "bg-amber-400", done: "bg-emerald-500", error: "bg-rose-500", running: "bg-sky-500 animate-pulse" };
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-900 text-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3.5">
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-teal-500/15 p-2"><FlaskConical className="h-5 w-5 text-teal-300" /></span>
            <div>
              <h1 className="text-base font-semibold">Ctrl+Alt+Cure</h1>
              <p className="text-xs text-slate-400">Evidence-checked biotech investment theses · UPMC Enterprises</p>
            </div>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-300">
            <span className="hidden sm:inline">{user.email}</span>
            <button className="rounded-lg p-1.5 hover:bg-slate-800" title="Sign out" onClick={() => base44.auth.logout(window.location.origin + "/")}><LogOut className="h-4 w-4" /></button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[15rem_1fr]">
        <aside className="space-y-3 lg:sticky lg:top-20 lg:self-start">
          <button className={`${primary} w-full`} onClick={() => setSelected(null)}><Plus className="h-4 w-4" /> New analysis</button>
          <div className={`${card} p-2`}>
            <p className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Analyses</p>
            {err && <p className="px-2 py-1 text-xs text-rose-700">{err}</p>}
            {runs.length === 0 && !err && <p className="px-2 py-1 text-sm text-slate-500">No analyses yet.</p>}
            {runs.map((r) => (
              <button key={r.run_id} onClick={() => setSelected(r.run_id)}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition hover:bg-slate-50 ${selected === r.run_id ? "bg-slate-100 font-medium" : ""}`}>
                <span className="min-w-0">
                  <span className="block truncate text-slate-800">{r.settings?.disease || "Analysis"} · {r.run_id}</span>
                  <span className="block text-xs text-slate-500">{r.settings?.llm === "stub" ? "test mode" : "live"} · {(r.started || "").slice(11, 16)}</span>
                </span>
                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot[r.status] || "bg-slate-300"}`} />
              </button>
            ))}
          </div>
        </aside>
        <main className="min-w-0">{selected ? <RunView runId={selected} /> : <NewRun onStarted={setSelected} />}</main>
      </div>
    </div>
  );
}
