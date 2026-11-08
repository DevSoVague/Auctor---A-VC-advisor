import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { base44 } from "@/api/base44Client";
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, ChevronDown, ChevronRight, Clock, Download,
  FileSpreadsheet, FileText, Layers, Loader2, LogOut, PauseCircle, Play, Plus,
  RefreshCw, ShieldCheck, Target, XCircle,
} from "lucide-react";

// ---------------------------------------------------------------- brand
// The mark: a keyboard keycap (Ctrl / Alt) carrying a medical cross (Cure), with the
// Ctrl caret in the corner. Teal-to-sky gradient on a dark key.
function Logo({ size = 36 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-label="Ctrl+Alt+Cure logo" role="img">
      <defs>
        <linearGradient id="cac-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#38bdf8" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="36" height="34" rx="9" fill="#0b1220" stroke="url(#cac-g)" strokeWidth="2" />
      <rect x="6" y="33" width="28" height="4" rx="2" fill="url(#cac-g)" opacity="0.35" />
      <path d="M8 12.5l3-3 3 3" fill="none" stroke="#94a3b8" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M17.5 11h5v6.5H29v5h-6.5V29h-5v-6.5H11v-5h6.5z" fill="url(#cac-g)" />
    </svg>
  );
}

const Wordmark = ({ className = "" }) => (
  <span className={`font-semibold tracking-tight ${className}`}>
    Ctrl<span className="text-teal-300">+</span>Alt<span className="text-teal-300">+</span>Cure
  </span>
);

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
  a0_intake: "Pitch intake", a1_disease: "Why now", a2_mechanism: "Mechanisms", a3_profile: "Product profile",
  a4_inactive: "Shelved & repurposable assets", a5_active: "Competitor landscape",
  a6_redteam: "Red team", a6b_judge: "Judge", a7_judge: "Judge", a7_citations: "Citation check",
};
const agentName = (a) => AGENTS[a] || a;

// Consistent status colours everywhere: green good, amber caution, red stop.
const TONE = {
  good: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  warn: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  bad: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  info: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  muted: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
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
const primary = `${btn} bg-teal-400 text-slate-950 hover:bg-teal-300 shadow-lg shadow-teal-500/20`;
const secondary = `${btn} bg-slate-800 text-slate-100 ring-1 ring-slate-700 hover:bg-slate-700`;
const HERO_BG = {
  backgroundColor: "#020617",
  backgroundImage:
    "radial-gradient(60rem 30rem at 0% 0%, rgba(45,212,191,0.18), transparent 60%), radial-gradient(40rem 24rem at 100% 100%, rgba(56,189,248,0.12), transparent 60%)",
};
const card = "rounded-2xl bg-slate-900/80 ring-1 ring-white/10 shadow-xl shadow-black/20";

function Section({ title, icon: Icon, children, right, className = "" }) {
  return (
    <section className={`${card} p-5 ${className}`}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-50">{Icon && <Icon className="h-4 w-4 text-teal-400" />}{title}</h3>
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
      <circle cx="44" cy="44" r={r} fill="none" stroke="#1e293b" strokeWidth="10" />
      <circle cx="44" cy="44" r={r} fill="none" stroke={tone} strokeWidth="10" strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`} transform="rotate(-90 44 44)" />
      <text x="44" y="49" textAnchor="middle" className="fill-slate-50 text-[17px] font-semibold">{total ? Math.round(pct * 100) : 0}%</text>
    </svg>
  );
}

function StackedBar({ parts, total }) {
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
      {parts.map((p, i) => p.value > 0 && (
        <div key={i} className={p.className} style={{ width: `${(p.value / Math.max(total, 1)) * 100}%` }} title={`${p.label}: ${p.value}`} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- pipeline tracker
// Person 3's graph (grounded1to8): each agent runs retrieve → prompt → toolloop → llm → parse →
// postcheck/validate → cite (the A7 citation check) → commit. Stages match on node-name prefixes.
const STAGES = [
  { key: "intake", label: "Intake", prefixes: ["setup", "intake"] },
  { key: "a1", label: "Why now", prefixes: ["a1_", "review_a1"] },
  { key: "a2", label: "Mechanisms", prefixes: ["a2_", "review_a2"] },
  { key: "a3", label: "Product profile", prefixes: ["a3_", "review_a3", "next_card"] },
  { key: "a45", label: "Competitors", prefixes: ["a4_", "a5_"] },
  { key: "a6", label: "Red team", prefixes: ["a6_retrieve", "a6_prompt", "a6_toolloop", "a6_llm", "a6_parse", "a6_fix", "a6_postcheck", "a6_validate", "a6_cite", "a6_commit"] },
  { key: "judge", label: "Judge", prefixes: ["a6b_judge", "a6_route", "review_a6"] },
  { key: "report", label: "Report", prefixes: ["a7_final", "report"] },
];
const stageOf = (node = "") => STAGES.findIndex((st) => st.prefixes.some((p) => node.startsWith(p)));

function Pipeline({ run }) {
  // The API returns the latest 60 steps, so a stage counts as done once any later stage has run.
  const steps = run.steps || [];
  const reached = Math.max(-1, ...steps.map((s) => stageOf(s.node)));
  const last = steps[steps.length - 1]?.node || "";
  const active = run.status === "running" || run.status === "paused";
  const currentIdx = stageOf(last);
  return (
    <ol className="flex flex-wrap items-center gap-y-3">
      {STAGES.map((st, i) => {
        const done = run.status === "done" || i < reached || (i === reached && !active);
        const current = active && i === currentIdx;
        const dot = current ? "bg-teal-400 text-slate-950 ring-4 ring-teal-400/25"
          : done ? "bg-emerald-500 text-slate-950" : "bg-slate-800 text-slate-400";
        return (
          <li key={st.key} className="flex items-center">
            <div className="flex items-center gap-2">
              <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${dot}`}>
                {done && !current ? <CheckCircle2 className="h-4 w-4" /> : current && run.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : i + 1}
              </span>
              <span className={`text-sm ${current ? "font-semibold text-slate-50" : done ? "text-slate-300" : "text-slate-500"}`}>{st.label}</span>
            </div>
            {i < STAGES.length - 1 && <span className={`mx-3 h-px w-6 sm:w-10 ${done ? "bg-emerald-400/70" : "bg-slate-700"}`} />}
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
    // Test mode by default: it costs nothing, and live runs need the engine's model key.
    disease: "COPD", as_of: "2021-01-01", mode: "guided", audience: "generalist",
    llm: "stub", profile: "dev", search: "corpus", max_cards: 0,
    pitch: "", cite_mode: "node", tools: false,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k) => (e) => {
    const value = e.target.type === "number" ? Number(e.target.value)
      : e.target.type === "checkbox" ? e.target.checked : e.target.value;
    // Test mode's canned answers cite the sample corpus up to 2020: fixed cutoff, corpus only, no tools.
    // Live runs default to the COPD evidence store plus web search.
    if (k === "llm") {
      return setF(value === "stub"
        ? { ...f, llm: value, as_of: "2021-01-01", search: "corpus", tools: false }
        : { ...f, llm: value, as_of: today, search: "evstore+web" });
    }
    setF({ ...f, [k]: value });
  };

  const start = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const body = { ...f, pitch: f.pitch.trim() || null };
      const out = await api("POST", "/runs", body);
      onStarted(out.data.run_id);
    } catch (x) { setErr(String(x.message || x)); } finally { setBusy(false); }
  };

  const field = "mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 text-sm focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-400/20";
  const label = "block text-sm font-medium text-slate-300";
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
        <div className="px-6 py-7 text-white" style={HERO_BG}>
          <h2 className="text-2xl font-semibold">New analysis</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-300">Turn one disease area into 2 to 3 evidence-checked pursuits an investor can act on this year.</p>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {steps.map(([t, d], i) => (
              <div key={t} className="rounded-xl bg-slate-900/5 p-3 ring-1 ring-white/10">
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
              <span className="mt-1 block text-xs text-slate-400">
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
              <select className={field} value={f.search} onChange={set("search")} disabled={f.llm === "stub"}>
                <option value="evstore+web">COPD evidence store + web search</option>
                <option value="evstore">COPD evidence store only</option>
                <option value="corpus">Sample corpus (test mode)</option>
              </select>
              <span className="mt-1 block text-xs text-slate-400">
                {f.llm === "stub" ? "Test mode always uses the sample corpus." : "120 dated papers, trials, labels and market data, embedded for search."}
              </span>
            </label>
            <label className={label}>Hypotheses to analyse in depth
              <input type="number" min={0} max={5} className={field} value={f.max_cards} onChange={set("max_cards")} />
              <span className="mt-1 block text-xs text-slate-400">0 = all ranked hypotheses.</span>
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
            <label className={label}>Citation checks
              <select className={field} value={f.cite_mode} onChange={set("cite_mode")}>
                <option value="node">After every agent (reruns an agent that cites badly)</option>
                <option value="end">Once, before the report</option>
                <option value="off">Off</option>
              </select>
            </label>
            <label className={`${label} sm:col-span-2`}>Startup pitch (optional)
              <input className={field} value={f.pitch} onChange={set("pitch")} placeholder="https://… link to a pitch deck or page" />
              <span className="mt-1 block text-xs text-slate-400">The intake agent screens it first, then the analysis checks its claims against the evidence.</span>
            </label>
            <label className="flex items-start gap-2 text-sm text-slate-300 sm:col-span-2">
              <input type="checkbox" className="mt-1 accent-teal-400" checked={f.tools} onChange={set("tools")} disabled={f.llm === "stub"} />
              <span>Let agents search the evidence store and the web as they work
                <span className="block text-xs text-slate-400">Live runs only. Why now can also add newly found papers and trials to the store.</span></span>
            </label>
          </div>
          {err && <p className="mt-4 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{err}</p>}
          <div className="mt-6 flex items-center justify-between">
            <p className="flex items-center gap-1.5 text-xs text-slate-400"><ShieldCheck className="h-4 w-4" /> Public sources only. Every claim is cited and checked.</p>
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
        <span className="font-medium text-slate-100">{name}</span>
        <Pill tone="good">{report.kept} kept</Pill><Pill tone="warn">{report.downgraded} downgraded</Pill><Pill tone="bad">{report.flagged} flagged</Pill>
      </div>
      {report.entries?.length > 0 && (
        <div className="max-h-56 overflow-auto rounded-lg ring-1 ring-white/10">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-slate-800/60 text-slate-400">
              <tr><th className="px-3 py-2">Claim</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">Dated</th><th className="px-3 py-2">Supports the claim?</th></tr>
            </thead>
            <tbody>
              {report.entries.map((e, i) => (
                <tr key={i} className="border-t border-white/5">
                  <td className="px-3 py-1.5 font-mono text-slate-400">{e.claim_id}</td>
                  <td className="px-3 py-1.5 font-mono text-slate-400">{e.source_id}</td>
                  <td className="px-3 py-1.5 text-slate-400">{e.source_date || "—"}</td>
                  <td className="px-3 py-1.5"><Pill tone={VERDICT[e.supports]}>{e.supports.replace("_", " ")}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.invalid_claims?.length > 0 && (
        <p className="mt-1 text-xs text-rose-300">{report.invalid_claims.length} claim(s) break the evidence rules: {report.invalid_claims.map((p) => p.claim_id).join(", ")}</p>
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
  if (!rows.length) return <p className="text-sm text-slate-400">Citation checks appear as each agent finishes.</p>;
  return (
    <div className="space-y-4">
      {rows.map(([a, t]) => {
        const total = t.kept + t.downgraded + t.flagged;
        return (
          <div key={a}>
            <div className="mb-1.5 flex justify-between text-sm"><span className="text-slate-300">{agentName(a)}</span><span className="text-slate-400">{t.checked} {t.checked === 1 ? "check" : "checks"}</span></div>
            <StackedBar total={total} parts={[
              { value: t.kept, label: "kept", className: "bg-emerald-500" },
              { value: t.downgraded, label: "downgraded", className: "bg-amber-400" },
              { value: t.flagged, label: "flagged", className: "bg-rose-500" },
            ]} />
          </div>
        );
      })}
      <div className="flex gap-4 pt-1 text-xs text-slate-400">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-emerald-500" />Supported</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-amber-400" />Partly supported</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-rose-500" />Missing, too late or unsupported</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- hypotheses
const LEVEL = { gold: "bg-amber-400/15 text-amber-300 ring-amber-400/40", silver: "bg-slate-300/10 text-slate-200 ring-white/15/30", bronze: "bg-orange-400/15 text-orange-300 ring-orange-400/40" };
const verdictTone = (v, killed) => (killed || v === "kill" ? "bad" : v === "survives" ? "good" : v ? "warn" : "muted");

const pendingLabel = (status) => (status === "running" || status === "paused" ? "queued" : "not analysed");

const JUDGE_TONE = { advance: "good", revise: "warn", kill: "bad" };
const SEVERITY_TONE = { fatal: "bad", major: "warn", minor: "muted" };
const list = (x) => (Array.isArray(x) ? x : []);

function Sub({ title, children }) {
  return (
    <div className="mt-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      <div className="mt-1.5 text-sm text-slate-300">{children}</div>
    </div>
  );
}

// Everything the run's result.json holds for one hypothesis: product profile (A3), shelved and
// repurposable assets (A4), the competitor landscape (A5), the red team's risks (A6), the judge (A6b).
function HypothesisDetails({ hid, result }) {
  const profile = result.profiles?.[hid];
  const inactive = result.assets?.[`${hid}:inactive`];
  const land = result.assets?.[`${hid}:landscape`];
  const verdict = result.verdicts?.[hid];
  const judge = result.judgments?.[hid];
  const revised = result.revise_used?.[hid];
  const programs = [...list(land?.programs), ...list(land?.orthogonal)];
  const shelved = [...list(inactive?.shelved), ...list(inactive?.repurposing)];
  if (!profile && !land && !inactive && !verdict && !judge) return null;
  return (
    <div className="mt-4 border-t border-white/10 pt-1">
      {judge && (
        <Sub title="Judge">
          <div className="flex flex-wrap items-center gap-1.5">
            <Pill tone={JUDGE_TONE[judge.decision] || "muted"}>{judge.decision}</Pill>
            {judge.red_team_is && <Pill>red team {judge.red_team_is.replace("_", " ")}</Pill>}
            {revised && <Pill tone="info">revised once</Pill>}
          </div>
          <ul className="mt-2 space-y-1">
            {list(judge.rulings).map((r, i) => (
              <li key={i} className="flex gap-2"><Pill tone={r.holds ? SEVERITY_TONE[r.severity] || "warn" : "good"}>{r.holds ? r.severity : "rejected"}</Pill><span>{r.risk}</span></li>
            ))}
          </ul>
        </Sub>
      )}
      {profile && (
        <Sub title="Winning product">
          {profile.soc_gap_at_launch && <p><span className="text-slate-400">Gap at launch: </span>{profile.soc_gap_at_launch}</p>}
          {list(profile.must_haves).length > 0 && <ul className="mt-1 list-disc pl-5">{profile.must_haves.map((m, i) => <li key={i}>{m}</li>)}</ul>}
        </Sub>
      )}
      {land && (
        <Sub title={`Competitor landscape · ${programs.length} programs`}>
          {land.crowding_score != null && (
            <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">Crowding
              <div className="h-1.5 w-28 overflow-hidden rounded-full bg-slate-800"><div className="h-full bg-amber-400" style={{ width: `${Math.round(Number(land.crowding_score) * 100)}%` }} /></div>
              {Math.round(Number(land.crowding_score) * 100)}%
            </div>
          )}
          <ul className="space-y-1">
            {programs.slice(0, 6).map((p, i) => (
              <li key={i} className="flex flex-wrap gap-x-2"><span className="font-medium text-slate-100">{p.asset}</span><span className="text-slate-400">{p.owner} · {p.stage}{p.deprioritized ? " · deprioritized" : ""}</span></li>
            ))}
          </ul>
          {list(land.catalysts).length > 0 && (
            <p className="mt-2 text-xs text-slate-400">Next catalysts: {land.catalysts.slice(0, 3).map((c) => `${c.what} (${c.on})`).join(" · ")}</p>
          )}
        </Sub>
      )}
      {shelved.length > 0 && (
        <Sub title={`Shelved & repurposable assets · ${shelved.length}`}>
          <ul className="space-y-1">
            {shelved.slice(0, 5).map((a, i) => (
              <li key={i}><span className="font-medium text-slate-100">{a.name}</span> <span className="text-slate-400">{a.owner} · {a.stage}{a.limitations ? ` · ${a.limitations}` : ""}</span></li>
            ))}
          </ul>
        </Sub>
      )}
      {list(verdict?.risks).length > 0 && (
        <Sub title="Red team risks">
          <ul className="space-y-1.5">
            {verdict.risks.map((r, i) => <li key={i}>{r.text}{r.kill_criterion && <span className="block text-xs text-slate-400">Kill if: {r.kill_criterion}</span>}</li>)}
          </ul>
        </Sub>
      )}
    </div>
  );
}

function HypothesisCard({ h, killed, current, runStatus, result }) {
  const v = killed ? "killed" : h.verdict || (current ? "in progress" : pendingLabel(runStatus));
  const [open, setOpen] = useState(false);
  const hasDetails = !!(result && (result.profiles?.[h.id] || result.verdicts?.[h.id] || result.judgments?.[h.id]));
  return (
    <div className={`${card} flex flex-col p-5 ${current ? "ring-2 ring-teal-400/60" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <span className="font-mono text-xs text-slate-500">{h.id}</span>
        <Pill tone={verdictTone(h.verdict, killed)}>{v}</Pill>
      </div>
      <h4 className="mt-2 text-base font-semibold leading-snug text-slate-50">{h.mechanism}</h4>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {h.validation_level && <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ${LEVEL[h.validation_level] || LEVEL.silver}`}>{h.validation_level}</span>}
        {h.evidence_tier != null && <Pill tone="info">Evidence tier {h.evidence_tier}</Pill>}
        {h.direction && <Pill>{h.direction}</Pill>}
        {h.modality && <Pill>{h.modality}</Pill>}
      </div>
      {h.top_risk && <p className="mt-4 text-sm text-slate-300"><span className="font-medium text-rose-300">Main risk: </span>{h.top_risk}</p>}
      {h.cheapest_test && <p className="mt-2 text-sm text-slate-300"><span className="font-medium text-teal-300">Cheapest test: </span>{h.cheapest_test}</p>}
      {h.judge_decision && <p className="pt-3 text-xs text-slate-400">Judge: {h.judge_decision}</p>}
      {hasDetails && (
        <button className="mt-auto flex items-center gap-1 pt-4 text-sm font-medium text-teal-300 hover:text-teal-200" onClick={() => setOpen(!open)}>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />} {open ? "Hide details" : "Profile, competitors, judge"}
        </button>
      )}
      {open && <HypothesisDetails hid={h.id} result={result} />}
    </div>
  );
}

// The A0 intake gate's view of a startup pitch: shown only when the run was given one.
function Intake({ intake }) {
  if (!intake || typeof intake !== "object") return null;
  const scalars = Object.entries(intake).filter(([, v]) => v != null && typeof v !== "object" && String(v).length < 200);
  const cited = list(intake.cited);
  return (
    <Section title="Startup pitch intake" icon={ShieldCheck}>
      <div className="flex flex-wrap gap-1.5">
        {scalars.slice(0, 8).map(([k, v]) => <Pill key={k} tone={v === true ? "good" : v === false ? "bad" : "muted"}>{k.replace(/_/g, " ")}: {String(v)}</Pill>)}
      </div>
      {cited.length > 0 && <p className="mt-3 text-sm text-slate-300">{cited.length} item(s) the pitch cites were found in, or added to, the evidence store and can be cited by the agents.</p>}
    </Section>
  );
}

function MemoBanner({ run }) {
  if (run.memo_error) return <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-200"><span className="font-semibold">Memo not written: </span>{run.memo_error}</p>;
  if (!run.memo) return null;
  const files = Object.entries(run.memo);
  return (
    <div className={`${card} flex flex-wrap items-center justify-between gap-3 p-4 ring-teal-400/40`}>
      <div className="flex items-center gap-3">
        <span className="rounded-lg bg-teal-400/10 p-2 text-teal-300"><FileText className="h-5 w-5" /></span>
        <div><p className="text-sm font-semibold text-slate-50">Investor memo ready</p><p className="text-xs text-slate-400">Written by Agent 8 from this run's checked evidence</p></div>
      </div>
      <div className="flex flex-wrap gap-2">
        {files.map(([kind, name]) => (
          <button key={name} className={secondary} onClick={() => download(run.run_id, name)}>
            <Download className="h-4 w-4" /> {kind === "docx" ? "Word" : kind === "pdf" ? "PDF" : kind === "xlsx" ? "Programs (Excel)" : name}
          </button>
        ))}
      </div>
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

  if (err && !cp) return <p className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{err}</p>;
  if (!cp) return <div className={`${card} p-5`}><Loader2 className="h-5 w-5 animate-spin text-slate-500" /></div>;
  return (
    <section className={`${card} overflow-hidden ring-2 ring-amber-400/50`}>
      <div className="flex items-center gap-2 bg-amber-400/10 px-5 py-3 text-amber-200">
        <PauseCircle className="h-5 w-5" />
        <h3 className="text-base font-semibold">Your review · {agentName(cp.checkpoint)}{cp.hypothesis_id ? ` · hypothesis ${cp.hypothesis_id}` : ""}</h3>
      </div>
      <div className="p-5">
        <ul className="space-y-1.5 text-sm text-slate-300">
          {(cp.summary || []).map((s, i) => <li key={i} className="flex gap-2"><ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />{s}</li>)}
        </ul>
        <div className="mt-4 rounded-xl bg-slate-800/60 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-100"><ShieldCheck className="h-4 w-4 text-teal-400" /> Citation check</div>
          {Object.keys(cp.citations || {}).length === 0
            ? <p className="mt-2 text-sm text-slate-400">No sourced claims to check at this step.</p>
            : Object.entries(cp.citations).map(([k, r]) => <Ledger key={k} name={agentName(k.split(":")[0]) + (k.split(":")[1] ? ` · ${k.split(":")[1]}` : "")} report={r} />)}
        </div>
        <button className="mt-4 flex items-center gap-1 text-sm text-slate-400 hover:text-slate-50" onClick={() => setShowOutput(!showOutput)}>
          {showOutput ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />} Full agent output
        </button>
        {showOutput && !editing && <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-black/40 p-3 text-xs text-slate-200">{JSON.stringify(cp.output, null, 2)}</pre>}
        {editing && <textarea className="mt-2 h-80 w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-slate-100 font-mono text-xs" value={edited} onChange={(e) => setEdited(e.target.value)} />}
        <label className="mt-4 block text-sm font-medium text-slate-300">Comment for the agent (needed to rerun)
          <textarea className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-slate-100 text-sm" rows={2} value={comment}
            placeholder="e.g. Restrict the segment to former smokers with eosinophils ≥300" onChange={(e) => setComment(e.target.value)} />
        </label>
        {err && <p className="mt-3 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{err}</p>}
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
  { match: (n) => n === "memo.docx", label: "Investor memo (Word)", icon: FileText },
  { match: (n) => n === "memo.pdf", label: "Investor memo (PDF)", icon: FileText },
  { match: (n) => n === "report.md", label: "Full report", icon: FileText },
  { match: (n) => n === "programs.xlsx", label: "Programs sheet (Excel)", icon: FileSpreadsheet },
  { match: (n) => n === "analysis.xlsx", label: "Analysis workbook (Excel)", icon: FileSpreadsheet },
  { match: (n) => n === "result.json", label: "Structured results (JSON)", icon: Layers },
  { match: (n) => n === "failures.log", label: "Failures log", icon: AlertTriangle },
];

function Downloads({ runId }) {
  const [files, setFiles] = useState([]);
  useEffect(() => { api("GET", `/runs/${runId}/files`).then((o) => setFiles(o.data || [])).catch(() => {}); }, [runId]);
  const primaryFiles = files.map((f) => ({ f, kind: PRIMARY_FILES.find((p) => p.match(f.name)) })).filter((x) => x.kind);
  const technical = files.filter((f) => !PRIMARY_FILES.some((p) => p.match(f.name)));
  return (
    <Section title="Downloads" icon={Download}>
      {files.length === 0 ? <p className="text-sm text-slate-400">Files appear when the run finishes.</p> : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {primaryFiles.map(({ f, kind }) => {
              const Icon = kind.icon;
              return (
                <button key={f.name} onClick={() => download(runId, f.name)} className="flex items-center gap-3 rounded-xl p-3 text-left ring-1 ring-white/10 transition hover:bg-white/5 hover:ring-teal-400/50">
                  <span className="rounded-lg bg-teal-400/10 p-2 text-teal-300"><Icon className="h-5 w-5" /></span>
                  <span><span className="block text-sm font-medium text-slate-50">{kind.label}</span><span className="text-xs text-slate-400">{f.name}</span></span>
                </button>
              );
            })}
          </div>
          {technical.length > 0 && (
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer text-slate-400 hover:text-slate-100">Technical files ({technical.length})</summary>
              <div className="mt-2 flex flex-wrap gap-2">
                {technical.map((f) => <button key={f.name} className="rounded-lg px-2.5 py-1 text-xs ring-1 ring-white/10 hover:bg-white/5" onClick={() => download(runId, f.name)}>{f.name}</button>)}
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
          <article className="prose prose-invert max-w-none prose-a:text-teal-300 prose-strong:text-slate-50 prose-headings:font-semibold prose-h1:text-2xl prose-h2:mt-8 prose-h2:border-b prose-h2:border-white/10 prose-h2:pb-2 prose-table:text-sm prose-th:bg-slate-800 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-td:align-top">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
          </article>
        ) : <p className="text-sm text-slate-400">The report appears when the run finishes.</p>}
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
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
        <p className="mt-1 text-2xl font-semibold text-slate-50">{value}</p>
        {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
      </div>
    </div>
  );
}

function RunView({ runId }) {
  const [run, setRun] = useState(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  const [tab, setTab] = useState("overview");
  const [result, setResult] = useState(null);

  // result.json is written when the run finishes: profiles, A4/A5 assets, verdicts, judgments, intake.
  const finished = run?.status === "done";
  useEffect(() => {
    setResult(null);
    if (!finished) return;
    api("GET", `/runs/${runId}/files/result.json`)
      .then((o) => setResult(o.data && typeof o.data === "object" ? o.data : JSON.parse(o.text || "null")))
      .catch(() => {});
  }, [runId, finished]);

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

  if (err && !run) return <p className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{err}</p>;
  if (!run) return <div className="flex h-64 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-slate-500" /></div>;

  const s = run.settings || {};
  return (
    <div className="space-y-5">
      <div className={`${card} p-6`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold text-slate-50">{s.disease || "Analysis"} <span className="font-normal text-slate-500">· run {run.run_id}</span></h2>
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

      {run.error && <p className="rounded-lg bg-rose-500/10 p-4 text-sm text-rose-300"><span className="font-semibold">The run stopped: </span>{run.error}</p>}
      <MemoBanner run={run} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Hypotheses" value={stats.hs.length || "—"} sub={stats.hs.length ? `${stats.survived} survived · ${stats.killed.size} killed` : "Ranked after Mechanisms"} />
        <Stat label="Citation integrity" value={`${stats.kept}/${stats.total}`} sub="claim-source pairs supported"><Ring value={stats.kept} total={stats.total} /></Stat>
        <Stat label="Flagged citations" value={stats.flagged} sub="missing, too late or unsupported" />
        <Stat label="Model cost" value={`$${(run.cost?.total_usd ?? 0).toFixed(4)}`} sub={`${run.step_count} steps`} />
      </div>

      {run.status === "paused" && <Review key={`${run.run_id}-${run.step_count}`} runId={run.run_id} onDone={load} />}

      <nav className="flex gap-1 overflow-x-auto rounded-xl bg-slate-900 p-1 ring-1 ring-white/10">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`flex items-center gap-2 whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition ${tab === t.key ? "bg-slate-900 text-white" : "text-slate-400 hover:bg-white/10"}`}>
              <Icon className="h-4 w-4" />{t.label}
            </button>
          );
        })}
      </nav>

      {tab === "overview" && result?.intake && <Intake intake={result.intake} />}
      {tab === "overview" && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
          <Section title="Hypotheses at a glance" icon={Target} className="lg:col-span-3">
            {stats.hs.length ? (
              <ul className="divide-y divide-white/5">
                {stats.hs.map((h) => (
                  <li key={h.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-50">{h.mechanism}</p>
                      <p className="text-xs text-slate-400">{h.id} · {h.validation_level || "—"} · tier {h.evidence_tier ?? "—"}</p>
                    </div>
                    <Pill tone={verdictTone(h.verdict, stats.killed.has(h.id))}>{stats.killed.has(h.id) ? "killed" : h.verdict || (run.current_hypothesis === h.id && run.status !== "done" ? "in progress" : pendingLabel(run.status))}</Pill>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-slate-400">Ranked after the Mechanisms agent runs.</p>}
          </Section>
          <Section title="Evidence checks by agent" icon={ShieldCheck} className="lg:col-span-2"><EvidenceChart citations={run.citations} /></Section>
        </div>
      )}
      {tab === "hypotheses" && (
        stats.hs.length
          ? <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">{stats.hs.map((h) => <HypothesisCard key={h.id} h={h} killed={stats.killed.has(h.id)} current={run.current_hypothesis === h.id && run.status !== "done"} runStatus={run.status} result={result} />)}</div>
          : <Section title="Hypotheses" icon={Target}><p className="text-sm text-slate-400">Ranked after the Mechanisms agent runs.</p></Section>
      )}
      {tab === "evidence" && <Section title="Evidence checks by agent" icon={ShieldCheck}><EvidenceChart citations={run.citations} /></Section>}
      {tab === "report" && <Report runId={run.run_id} done={run.status === "done"} />}
      {tab === "activity" && (
        <Section title={`Activity (${run.step_count} steps)`} icon={Activity}>
          <ol className="max-h-[32rem] space-y-1 overflow-auto text-sm">
            {[...(run.steps || [])].reverse().map((st, i) => {
              const cite = /_cite$/.test(st.node || "") || st.node === "a7_final"; // the A7 citation check
              return (
              <li key={i} className={`flex gap-3 rounded-lg px-2 py-1.5 ${cite ? "bg-teal-400/10" : ""}`}>
                {cite ? <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal-400" /> : <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" />}
                <span className="w-36 shrink-0 font-mono text-xs leading-5 text-slate-400">{st.node}{st.hid ? ` · ${st.hid}` : ""}</span>
                <span className="text-slate-300">{st.detail}</span>
              </li>
              );
            })}
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

  const field = "mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 text-sm focus:border-teal-400 focus:outline-none focus:ring-2 focus:ring-teal-400/20";
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4" style={HERO_BG}>
      <div className="w-full max-w-sm rounded-2xl bg-slate-900 p-8 shadow-2xl ring-1 ring-white/10">
        <div className="flex items-center gap-3">
          <Logo size={44} />
          <div>
            <h1 className="text-xl text-slate-50"><Wordmark /></h1>
            <p className="text-xs text-slate-400">UPMC Enterprises · sign in to continue</p>
          </div>
        </div>
        <button className={`${primary} mt-6 w-full`} onClick={() => base44.auth.loginWithProvider("google", window.location.origin + "/")}>Continue with Google</button>
        <div className="my-5 flex items-center gap-3 text-xs text-slate-500"><span className="h-px flex-1 bg-slate-700" />or<span className="h-px flex-1 bg-slate-700" /></div>
        <form onSubmit={withPassword} className="space-y-3">
          <label className="block text-sm font-medium text-slate-300">Email<input type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label className="block text-sm font-medium text-slate-300">Password<input type="password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {err && <p className="rounded-lg bg-rose-500/10 p-2 text-sm text-rose-300">{err}</p>}
          <button className={`${secondary} w-full`} disabled={busy}>{busy && <Loader2 className="h-4 w-4 animate-spin" />} Sign in</button>
        </form>
        <p className="mt-5 flex items-center gap-1.5 text-xs text-slate-400"><ShieldCheck className="h-3.5 w-3.5" /> Only signed-in users can start or review analyses.</p>
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
  const anyActive = runs.some((r) => r.status === "running" || r.status === "paused");
  useEffect(() => {
    if (!user || !anyActive) return;
    const t = setInterval(loadRuns, 4000);
    return () => clearInterval(t);
  }, [user, anyActive, loadRuns]);

  if (authState === "checking") return <div className="flex h-screen items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-slate-500" /></div>;
  if (authState === "out") return <SignIn />;

  const dot = { paused: "bg-amber-400", done: "bg-emerald-500", error: "bg-rose-500", running: "bg-sky-500 animate-pulse" };
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-10 border-b border-white/10 bg-slate-950/80 text-white backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3.5">
          <div className="flex items-center gap-3">
            <Logo size={36} />
            <div>
              <h1 className="text-base"><Wordmark /></h1>
              <p className="text-xs text-slate-500">Evidence-checked biotech investment theses · UPMC Enterprises</p>
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
            <p className="px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Analyses</p>
            {err && <p className="px-2 py-1 text-xs text-rose-300">{err}</p>}
            {runs.length === 0 && !err && <p className="px-2 py-1 text-sm text-slate-400">No analyses yet.</p>}
            {runs.map((r) => (
              <button key={r.run_id} onClick={() => setSelected(r.run_id)}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition hover:bg-white/5 ${selected === r.run_id ? "bg-slate-800 font-medium" : ""}`}>
                <span className="min-w-0">
                  <span className="block truncate text-slate-100">{r.settings?.disease || "Analysis"} · {r.run_id}</span>
                  <span className="block text-xs text-slate-400">{r.settings?.llm === "stub" ? "test mode" : "live"} · {(r.started || "").slice(11, 16)}</span>
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
