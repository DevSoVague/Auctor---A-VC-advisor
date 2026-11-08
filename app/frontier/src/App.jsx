import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { base44 } from "@/api/base44Client";
import { Loader2 } from "lucide-react";

// Auctor, design direction 1 ("Grotesk, dark"): charcoal ground, off-white type, 1px rules,
// uppercase tracked labels, one amber accent for "waiting for your review". Colours live in
// tailwind.config.js (ink, raised, paper, dim, line, edge, amber, ok, bad).

const Wordmark = () => <span className="text-[14px] font-bold uppercase tracking-[.14em]">Auctor</span>;

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
  a4_inactive: "Shelved assets", a5_active: "Competitors", a6_redteam: "Red team",
  a6b_judge: "Judge", a7_judge: "Judge", a7_citations: "Citation check",
};
const agentName = (a) => AGENTS[a] || a;
const REVIEW_NODE = { review_a1: "a1_disease", review_a2: "a2_mechanism", review_a3: "a3_profile", review_a6: "a6_redteam" };

const STATUS = {
  running: { label: "Running", cls: "text-paper" },
  paused: { label: "Review", cls: "text-amber" },
  done: { label: "Complete", cls: "text-dim" },
  error: { label: "Error", cls: "text-bad" },
  stopped: { label: "Stopped", cls: "text-dim" },
};
const statusOf = (s) => STATUS[s] || STATUS.stopped;

// ---------------------------------------------------------------- primitives
const Label = ({ children, className = "" }) => (
  <div className={`text-[11px] font-semibold uppercase tracking-[.14em] text-dim ${className}`}>{children}</div>
);
const Meta = ({ children }) => <div className="text-[13px] uppercase tracking-[.06em] text-dim">{children}</div>;
const Headline = ({ children }) => (
  <h1 className="mt-2.5 text-[52px] font-bold uppercase leading-[.92] tracking-[-.035em] sm:text-[72px] xl:text-[92px]" style={{ textWrap: "balance" }}>{children}</h1>
);
const btnBase = "flex h-[52px] w-full items-center justify-between px-5 text-[15px] transition disabled:pointer-events-none disabled:opacity-40";
const BtnPrimary = ({ children, arrow = true, busy, ...p }) => (
  <button className={`${btnBase} bg-paper font-semibold text-ink hover:bg-white`} {...p}>
    <span className="flex items-center gap-2">{busy && <Loader2 className="h-4 w-4 animate-spin" />}{children}</span>{arrow && <span>→</span>}
  </button>
);
const BtnLine = ({ children, ...p }) => (
  <button className={`${btnBase} border border-edge font-medium text-paper hover:border-dim`} {...p}>{children}</button>
);
const LinkBtn = ({ children, ...p }) => (
  <button className="text-[13px] font-medium uppercase tracking-[.06em] text-dim hover:text-paper" {...p}>{children}</button>
);
const Rule = ({ children, className = "" }) => <div className={`border-b border-line py-3 ${className}`}>{children}</div>;
const field = "mt-2 h-11 w-full border border-edge bg-transparent px-3 text-[15px] text-paper outline-none focus:border-paper disabled:text-dim";
const fieldLabel = "block text-[11px] font-semibold uppercase tracking-[.14em] text-dim";

function Bar({ parts, total }) {
  return (
    <div className="flex h-1.5 w-full overflow-hidden bg-line">
      {parts.map((p, i) => p.value > 0 && (
        <div key={i} className={p.cls} style={{ width: `${(p.value / Math.max(total, 1)) * 100}%` }} title={`${p.label}: ${p.value}`} />
      ))}
    </div>
  );
}

function Stats({ items }) {
  return (
    <div className="grid grid-cols-2 border-t border-line md:grid-cols-4">
      {items.map((it, i) => (
        // Two columns on phones (rule between them, rule above the second row), four in a row from md up.
        <div key={it.label} className={["border-line py-[18px] pr-6",
          i % 2 === 1 ? "pl-6" : "", i > 0 ? "md:pl-6" : "",
          i % 2 === 0 ? "border-r" : "", i < items.length - 1 ? "md:border-r" : "md:border-r-0",
          i >= 2 ? "border-t md:border-t-0" : ""].join(" ")}>
          <div className="text-[44px] font-semibold leading-none tracking-[-.03em]">{it.value}{it.of != null && <span className="text-dim">/{it.of}</span>}</div>
          <div className="mt-1.5 text-[13px] text-dim">{it.label}</div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- pipeline
// Person 3's graph: each agent runs retrieve → prompt → toolloop → llm → parse →
// postcheck/validate → cite (the A7 citation check) → commit. Stages match node-name prefixes.
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
  const active = run.status === "running" || run.status === "paused";
  const current = stageOf(steps[steps.length - 1]?.node || "");
  return (
    <ol className="grid grid-cols-4 gap-x-4 gap-y-4 lg:grid-cols-8">
      {STAGES.map((st, i) => {
        const done = run.status === "done" || i < reached || (i === reached && !active);
        const now = active && i === current;
        const tone = now ? (run.status === "paused" ? "text-amber border-amber" : "text-paper border-paper") : done ? "text-paper border-paper/60" : "text-dim border-line";
        return (
          <li key={st.key} className={`border-t-2 pt-2 ${tone}`}>
            <div className="text-[11px] font-semibold tracking-[.14em] opacity-70">{String(i + 1).padStart(2, "0")}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[13px] font-medium uppercase tracking-[.06em]">
              {now && run.status === "running" && <Loader2 className="h-3 w-3 animate-spin" />}{st.label}
            </div>
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
      const out = await api("POST", "/runs", { ...f, pitch: f.pitch.trim() || null });
      onStarted(out.data.run_id);
    } catch (x) { setErr(String(x.message || x)); } finally { setBusy(false); }
  };

  const steps = [
    ["Why now", "Shifts in endpoints, segments and trial tools"],
    ["Mechanisms", "Ranked hypotheses with evidence tiers"],
    ["Winning product", "Must-haves and the gap at launch"],
    ["Competitors", "Every active and shelved program"],
    ["Red team + judge", "What could kill each hypothesis"],
    ["Citation check", "Every claim checked against its source"],
  ];
  const stub = f.llm === "stub";
  return (
    <form onSubmit={start} className="flex flex-col gap-9">
      <div>
        <Meta>New analysis · {stub ? "test mode, no model cost" : "live models"}</Meta>
        <Headline>New<br />analysis.</Headline>
      </div>

      <div className="grid grid-cols-2 border-t border-line md:grid-cols-3 xl:grid-cols-6">
        {steps.map(([t, d], i) => (
          <div key={t} className="border-b border-line py-4 pr-4 xl:border-b-0">
            <div className="text-[11px] font-semibold tracking-[.14em] text-dim">{String(i + 1).padStart(2, "0")}</div>
            <div className="mt-1 text-[15px] font-medium">{t}</div>
            <div className="mt-0.5 text-[13px] leading-snug text-dim">{d}</div>
          </div>
        ))}
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="grid grid-cols-1 gap-x-8 gap-y-6 sm:grid-cols-2">
          <label className={fieldLabel}>Disease area<input className={field} value={f.disease} onChange={set("disease")} /></label>
          <label className={fieldLabel}>Evidence cutoff
            <input type="date" className={field} value={f.as_of} onChange={set("as_of")} disabled={stub} />
            <span className="mt-1.5 block text-[12px] normal-case tracking-normal text-dim">{stub ? "Test mode's sample evidence stops in 2020, so the cutoff is 2021-01-01." : "Nothing published after this date is used."}</span>
          </label>
          <label className={fieldLabel}>Models
            <select className={field} value={f.llm} onChange={set("llm")}>
              <option value="stub">Test mode (no model calls, no cost)</option>
              <option value="live">Live models</option>
            </select>
          </label>
          <label className={fieldLabel}>Model profile
            <select className={field} value={f.profile} onChange={set("profile")}>
              <option value="dev">Fast</option>
              <option value="final">Final (strongest models)</option>
            </select>
          </label>
          <label className={fieldLabel}>Review mode
            <select className={field} value={f.mode} onChange={set("mode")}>
              <option value="guided">Guided: pause for my review after each agent</option>
              <option value="single_shot">Single shot: run straight through</option>
            </select>
          </label>
          <label className={fieldLabel}>Written for
            <select className={field} value={f.audience} onChange={set("audience")}>
              <option value="generalist">Generalist investor</option>
              <option value="specialist">Clinical specialist</option>
            </select>
          </label>
          <label className={fieldLabel}>Evidence source
            <select className={field} value={f.search} onChange={set("search")} disabled={stub}>
              <option value="evstore+web">COPD evidence store + web search</option>
              <option value="evstore">COPD evidence store only</option>
              <option value="corpus">Sample corpus (test mode)</option>
            </select>
          </label>
          <label className={fieldLabel}>Citation checks
            <select className={field} value={f.cite_mode} onChange={set("cite_mode")}>
              <option value="node">After every agent</option>
              <option value="end">Once, before the report</option>
              <option value="off">Off</option>
            </select>
          </label>
          <label className={fieldLabel}>Hypotheses in depth
            <input type="number" min={0} max={5} className={field} value={f.max_cards} onChange={set("max_cards")} />
            <span className="mt-1.5 block text-[12px] normal-case tracking-normal text-dim">0 = all ranked hypotheses.</span>
          </label>
          <label className={fieldLabel}>Startup pitch (optional)
            <input className={field} value={f.pitch} onChange={set("pitch")} placeholder="https://…" />
            <span className="mt-1.5 block text-[12px] normal-case tracking-normal text-dim">Screened first; its claims are checked against the evidence.</span>
          </label>
          <label className={`flex items-start gap-3 text-[14px] text-paper sm:col-span-2 ${stub ? "opacity-40" : ""}`}>
            <input type="checkbox" className="mt-1 accent-[#ECEAE4]" checked={f.tools} onChange={set("tools")} disabled={stub} />
            <span>Let agents search the evidence store and the web as they work
              <span className="block text-[13px] text-dim">Live runs only. Why now can also add newly found papers and trials to the store.</span></span>
          </label>
        </div>
        <div className="flex flex-col gap-3 self-start">
          <BtnPrimary type="submit" disabled={busy} busy={busy}>Start analysis</BtnPrimary>
          {err && <p className="border-l-2 border-bad pl-3 text-[13px] text-bad">{err}</p>}
          <p className="mt-2 text-[13px] leading-[1.45] text-dim">Public sources only. Every claim is cited, and every citation is checked against its source before you see it.</p>
        </div>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- citations
const SUPPORT = { yes: "text-ok", partly: "text-amber", no: "text-bad", not_found: "text-bad", after_cutoff: "text-bad" };

function Ledger({ name, report }) {
  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2">
        <span className="text-[14px] font-medium">{name}</span>
        <span className="text-[13px] text-dim">{report.kept} kept · {report.downgraded} downgraded · <span className={report.flagged ? "text-bad" : ""}>{report.flagged} flagged</span></span>
      </div>
      {report.entries?.length > 0 && (
        <div className="max-h-60 overflow-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="text-[11px] uppercase tracking-[.14em] text-dim">
              <tr><th className="py-2 pr-3 font-semibold">Claim</th><th className="py-2 pr-3 font-semibold">Source</th><th className="py-2 pr-3 font-semibold">Dated</th><th className="py-2 font-semibold">Holds?</th></tr>
            </thead>
            <tbody>
              {report.entries.map((e, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-1.5 pr-3 font-mono text-[12px] text-dim">{e.claim_id}</td>
                  <td className="py-1.5 pr-3 font-mono text-[12px] text-dim">{e.source_id}</td>
                  <td className="py-1.5 pr-3 text-dim">{e.source_date || "—"}</td>
                  <td className={`py-1.5 ${SUPPORT[e.supports] || "text-dim"}`}>{(e.supports || "").replace("_", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
  if (!rows.length) return <p className="py-3 text-[14px] text-dim">Citation checks appear as each agent finishes.</p>;
  return (
    <div>
      {rows.map(([a, t]) => {
        const total = t.kept + t.downgraded + t.flagged;
        return (
          <div key={a} className="border-b border-line py-3">
            <div className="mb-2 flex justify-between text-[14px]"><span>{agentName(a)}</span><span className="text-dim">{t.kept}/{total} hold</span></div>
            <Bar total={total} parts={[
              { value: t.kept, label: "supported", cls: "bg-paper" },
              { value: t.downgraded, label: "partly", cls: "bg-amber" },
              { value: t.flagged, label: "flagged", cls: "bg-bad" },
            ]} />
          </div>
        );
      })}
      <div className="flex flex-wrap gap-4 pt-3 text-[12px] text-dim">
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-3 bg-paper" />Supported</span>
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-3 bg-amber" />Partly</span>
        <span className="flex items-center gap-1.5"><span className="h-1.5 w-3 bg-bad" />Missing, too late or unsupported</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- hypotheses
const verdictCls = (v, killed) => (killed || v === "kill" ? "text-bad" : v === "survives" ? "text-ok" : v ? "text-amber" : "text-dim");
const pendingLabel = (status) => (status === "running" || status === "paused" ? "queued" : "not analysed");
const JUDGE = { advance: "text-ok", revise: "text-amber", kill: "text-bad" };
const SEVERITY = { fatal: "text-bad", major: "text-amber", minor: "text-dim" };
const list = (x) => (Array.isArray(x) ? x : []);

function Sub({ title, children }) {
  return (
    <div className="mt-5">
      <Label className="border-b border-line pb-2">{title}</Label>
      <div className="mt-2 text-[14px] leading-[1.45]">{children}</div>
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
  return (
    <div className="mt-2">
      {judge && (
        <Sub title="Judge">
          <p><span className={`font-semibold uppercase tracking-[.06em] ${JUDGE[judge.decision] || ""}`}>{judge.decision}</span>
            {judge.red_team_is && <span className="text-dim"> · red team {judge.red_team_is.replace("_", " ")}</span>}
            {revised && <span className="text-dim"> · revised once</span>}</p>
          {list(judge.rulings).map((r, i) => (
            <p key={i} className="mt-1.5 flex gap-3"><span className={`w-16 shrink-0 text-[12px] font-semibold uppercase tracking-[.06em] ${r.holds ? SEVERITY[r.severity] || "text-amber" : "text-ok"}`}>{r.holds ? r.severity : "rejected"}</span><span>{r.risk}</span></p>
          ))}
        </Sub>
      )}
      {profile && (
        <Sub title="Winning product">
          {profile.soc_gap_at_launch && <p><span className="text-dim">Gap at launch: </span>{profile.soc_gap_at_launch}</p>}
          {list(profile.must_haves).map((m, i) => <p key={i} className="mt-1 flex gap-3"><span className="text-dim">—</span>{m}</p>)}
        </Sub>
      )}
      {land && (
        <Sub title={`Competitors · ${programs.length} programs${land.crowding_score != null ? ` · ${Math.round(Number(land.crowding_score) * 100)}% crowded` : ""}`}>
          {programs.slice(0, 6).map((p, i) => (
            <p key={i} className="mt-1 flex justify-between gap-3"><span>{p.asset}</span><span className="shrink-0 text-dim">{p.owner} · {p.stage}{p.deprioritized ? " · deprioritized" : ""}</span></p>
          ))}
          {list(land.catalysts).length > 0 && <p className="mt-2 text-[13px] text-dim">Next: {land.catalysts.slice(0, 3).map((c) => `${c.what} (${c.on})`).join(" · ")}</p>}
        </Sub>
      )}
      {shelved.length > 0 && (
        <Sub title={`Shelved & repurposable · ${shelved.length}`}>
          {shelved.slice(0, 5).map((a, i) => (
            <p key={i} className="mt-1"><span>{a.name}</span> <span className="text-dim">{a.owner} · {a.stage}{a.limitations ? ` · ${a.limitations}` : ""}</span></p>
          ))}
        </Sub>
      )}
      {list(verdict?.risks).length > 0 && (
        <Sub title="Red team risks">
          {verdict.risks.map((r, i) => <p key={i} className="mt-1.5">{r.text}{r.kill_criterion && <span className="block text-[13px] text-dim">Kill if: {r.kill_criterion}</span>}</p>)}
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
    <div className={`border-t py-5 ${current ? "border-paper" : "border-line"}`}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-dim">{h.id}{h.validation_level ? ` · ${h.validation_level}` : ""}{h.evidence_tier != null ? ` · tier ${h.evidence_tier}` : ""}</span>
        <span className={`text-[13px] font-semibold uppercase tracking-[.06em] ${verdictCls(h.verdict, killed)}`}>{v}</span>
      </div>
      <h4 className="mt-2 text-[22px] font-semibold leading-tight tracking-[-.01em]">{h.mechanism}</h4>
      {(h.direction || h.modality) && <p className="mt-1 text-[13px] text-dim">{[h.direction, h.modality].filter(Boolean).join(" · ")}</p>}
      {h.top_risk && <p className="mt-3 flex gap-3 text-[14px] leading-[1.45]"><span className="w-24 shrink-0 text-dim">Main risk</span><span>{h.top_risk}</span></p>}
      {h.cheapest_test && <p className="mt-1.5 flex gap-3 text-[14px] leading-[1.45]"><span className="w-24 shrink-0 text-dim">Cheapest test</span><span>{h.cheapest_test}</span></p>}
      {h.judge_decision && <p className="mt-1.5 flex gap-3 text-[14px]"><span className="w-24 shrink-0 text-dim">Judge</span><span className={JUDGE[h.judge_decision] || ""}>{h.judge_decision}</span></p>}
      {hasDetails && <div className="mt-4"><LinkBtn onClick={() => setOpen(!open)}>{open ? "Hide details −" : "Profile, competitors, judge +"}</LinkBtn></div>}
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
    <div>
      <Label className="border-b border-line pb-3">Startup pitch intake</Label>
      {scalars.slice(0, 8).map(([k, v]) => (
        <Rule key={k} className="flex gap-4 text-[14px]"><span className="w-40 shrink-0 text-dim">{k.replace(/_/g, " ")}</span><span className={v === false ? "text-bad" : ""}>{String(v)}</span></Rule>
      ))}
      {cited.length > 0 && <p className="pt-3 text-[13px] text-dim">{cited.length} item(s) the pitch cites were found in, or added to, the evidence store.</p>}
    </div>
  );
}

// ---------------------------------------------------------------- review
// "2018-11-15 segment: GOLD 2019 made ..." → a dated row, as in the design.
const parseLine = (s) => {
  const m = /^(\d{4}-\d{2}-\d{2})\s+([\w-]+):\s*(.*)$/.exec(s || "");
  return m ? { date: m[1], kind: m[2], text: m[3] } : { date: "", kind: "", text: s };
};

function Review({ runId, onDone }) {
  const [cp, setCp] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [showChecks, setShowChecks] = useState(false);
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

  if (err && !cp) return <p className="border-l-2 border-bad pl-3 text-[14px] text-bad">{err}</p>;
  if (!cp) return <Loader2 className="h-5 w-5 animate-spin text-dim" />;
  const cites = Object.values(cp.citations || {});
  const kept = cites.reduce((n, r) => n + (r.kept || 0), 0);
  const total = cites.reduce((n, r) => n + (r.kept || 0) + (r.downgraded || 0) + (r.flagged || 0), 0);
  const lines = (cp.summary || []).map(parseLine);
  return (
    <div className="grid min-w-0 grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0">
        <Label className="border-b border-line pb-3">{agentName(cp.checkpoint)}{cp.hypothesis_id ? ` · ${cp.hypothesis_id}` : ""} · {lines.length} {lines.length === 1 ? "item" : "items"}</Label>
        {lines.map((l, i) => (
          <div key={i} className="flex gap-4 border-b border-line py-3 text-[15px] leading-[1.4]">
            {l.date && <span className="w-[88px] shrink-0 text-dim">{l.date}</span>}
            <span>{l.text}{l.kind && <span className="ml-2 text-[12px] uppercase tracking-[.06em] text-dim">{l.kind.replace("_", " ")}</span>}</span>
          </div>
        ))}

        <div className="mt-6 flex flex-wrap gap-6">
          <LinkBtn onClick={() => setShowChecks(!showChecks)}>{showChecks ? "Hide" : "Show"} citation check {total ? `· ${kept}/${total}` : ""}</LinkBtn>
          <LinkBtn onClick={() => setShowOutput(!showOutput)}>{showOutput ? "Hide" : "Show"} full output</LinkBtn>
        </div>
        {showChecks && (Object.keys(cp.citations || {}).length === 0
          ? <p className="mt-3 text-[14px] text-dim">No sourced claims to check at this step.</p>
          : Object.entries(cp.citations).map(([k, r]) => <Ledger key={k} name={agentName(k.split(":")[0]) + (k.split(":")[1] ? ` · ${k.split(":")[1]}` : "")} report={r} />))}
        {showOutput && !editing && <pre className="mt-3 max-h-80 overflow-auto border border-line bg-raised p-3 text-[12px] text-paper">{JSON.stringify(cp.output, null, 2)}</pre>}
        {editing && <textarea className="mt-3 h-80 w-full border border-edge bg-raised p-3 font-mono text-[12px] text-paper outline-none focus:border-paper" value={edited} onChange={(e) => setEdited(e.target.value)} />}

        <label className={`${fieldLabel} mt-6`}>Comment for the agent (needed to rerun)
          <textarea className="mt-2 w-full border border-edge bg-transparent p-3 text-[15px] normal-case tracking-normal text-paper outline-none focus:border-paper" rows={2} value={comment}
            placeholder="e.g. Restrict the segment to former smokers with eosinophils ≥300" onChange={(e) => setComment(e.target.value)} />
        </label>
      </div>
      <div className="flex flex-col gap-3 self-start">
        <BtnPrimary disabled={busy} busy={busy} onClick={() => decide("approve")}>Approve</BtnPrimary>
        <BtnLine disabled={busy} onClick={() => decide("approve_all")}>Approve the rest</BtnLine>
        <BtnLine disabled={busy} onClick={() => decide("rerun")}>Rerun with comment</BtnLine>
        {!editing
          ? <BtnLine disabled={busy} onClick={() => { setEditing(true); setShowOutput(true); }}>Edit output</BtnLine>
          : <BtnLine disabled={busy} onClick={() => decide("edit")}>Save edit and continue</BtnLine>}
        {err && <p className="border-l-2 border-bad pl-3 text-[13px] text-bad">{err}</p>}
        <p className="mt-2 text-[13px] leading-[1.45] text-dim">{total ? `Every claim above was checked against its source. ${kept} of ${total} hold.` : "No sourced claims at this step."}</p>
      </div>
    </div>
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
  { match: (n) => n === "memo.docx", label: "Investor memo · Word" },
  { match: (n) => n === "memo.pdf", label: "Investor memo · PDF" },
  { match: (n) => n === "report.md", label: "Full report" },
  { match: (n) => n === "programs.xlsx", label: "Programs sheet · Excel" },
  { match: (n) => n === "analysis.xlsx", label: "Analysis workbook · Excel" },
  { match: (n) => n === "result.json", label: "Structured results · JSON" },
  { match: (n) => n === "failures.log", label: "Failures log" },
];

function Downloads({ runId }) {
  const [files, setFiles] = useState([]);
  useEffect(() => { api("GET", `/runs/${runId}/files`).then((o) => setFiles(o.data || [])).catch(() => {}); }, [runId]);
  const primaryFiles = files.map((f) => ({ f, kind: PRIMARY_FILES.find((p) => p.match(f.name)) })).filter((x) => x.kind);
  const technical = files.filter((f) => !PRIMARY_FILES.some((p) => p.match(f.name)));
  return (
    <div>
      <Label className="border-b border-line pb-3">Downloads</Label>
      {files.length === 0 ? <p className="py-3 text-[14px] text-dim">Files appear when the run finishes.</p> : (
        <>
          {primaryFiles.map(({ f, kind }) => (
            <button key={f.name} onClick={() => download(runId, f.name)} className="flex w-full items-center justify-between border-b border-line py-3 text-left text-[15px] hover:text-white">
              <span>{kind.label}</span><span className="text-[13px] text-dim">{f.name} ↓</span>
            </button>
          ))}
          {technical.length > 0 && (
            <details className="pt-3 text-[13px]">
              <summary className="cursor-pointer uppercase tracking-[.06em] text-dim hover:text-paper">Technical files ({technical.length})</summary>
              <div className="mt-2 flex flex-wrap gap-2">
                {technical.map((f) => <button key={f.name} className="border border-edge px-2.5 py-1 text-[12px] hover:border-dim" onClick={() => download(runId, f.name)}>{f.name}</button>)}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function Report({ runId, done }) {
  const [report, setReport] = useState("");
  useEffect(() => { if (done) api("GET", `/runs/${runId}/report`).then((o) => setReport(o.text || "")).catch(() => {}); }, [runId, done]);
  return (
    <div className="grid min-w-0 grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0">
        <Label className="border-b border-line pb-3">Report</Label>
        {report ? (
          <article className="prose prose-invert mt-4 max-w-none prose-headings:font-semibold prose-headings:tracking-[-.01em] prose-h1:text-[32px] prose-h2:mt-10 prose-h2:border-b prose-h2:border-line prose-h2:pb-2 prose-h2:text-[22px] prose-p:text-paper prose-li:text-paper prose-strong:text-white prose-a:text-paper prose-a:underline prose-table:text-[13px] prose-th:border-b prose-th:border-line prose-th:px-3 prose-th:py-2 prose-th:text-dim prose-td:border-line prose-td:px-3 prose-td:py-2 prose-td:align-top">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
          </article>
        ) : <p className="py-3 text-[14px] text-dim">The report appears when the run finishes.</p>}
      </div>
      <div className="self-start"><Downloads runId={runId} /></div>
    </div>
  );
}

function MemoStrip({ run }) {
  if (run.memo_error) return <p className="border-l-2 border-amber pl-3 text-[14px] text-amber">Memo not written: {run.memo_error}</p>;
  if (!run.memo) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 border-y border-line py-4">
      <div><div className="text-[15px] font-semibold">Investor memo ready</div><div className="text-[13px] text-dim">Written by Agent 8 from this run's checked evidence</div></div>
      <div className="flex flex-wrap gap-6">
        {Object.entries(run.memo).map(([kind, name]) => (
          <LinkBtn key={name} onClick={() => download(run.run_id, name)}>{kind === "docx" ? "Word" : kind === "pdf" ? "PDF" : kind === "xlsx" ? "Programs · Excel" : name} ↓</LinkBtn>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- run view
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "hypotheses", label: "Hypotheses" },
  { key: "evidence", label: "Evidence checks" },
  { key: "report", label: "Report" },
  { key: "activity", label: "Activity" },
];

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

  if (err && !run) return <p className="border-l-2 border-bad pl-3 text-[14px] text-bad">{err}</p>;
  if (!run) return <div className="flex h-64 items-center"><Loader2 className="h-6 w-6 animate-spin text-dim" /></div>;

  const s = run.settings || {};
  const st = statusOf(run.status);
  const reviewAgent = REVIEW_NODE[(run.next || []).find((n) => REVIEW_NODE[n])];
  const stage = STAGES[stageOf((run.steps || [])[(run.steps || []).length - 1]?.node || "")]?.label;
  const headline = run.status === "paused" ? [`${agentName(reviewAgent) || stage || "Step"},`, "for review."]
    : run.status === "running" ? [`${stage || "Starting"},`, "running."]
    : run.status === "done" ? [`${s.disease || "Analysis"},`, "complete."]
    : run.status === "error" ? ["Stopped."] : ["Stopped."];
  const statusText = run.status === "paused" ? "waiting for your review" : run.status === "running" ? "running" : run.status === "done" ? "complete" : "stopped";

  return (
    <div className="flex flex-col gap-9">
      <div>
        <Meta>
          {s.disease || "Analysis"} · evidence to {s.as_of || "today"} · {s.mode === "single_shot" ? "single shot" : "guided"} · {s.llm === "stub" ? "test mode" : `live, ${s.profile === "final" ? "final" : "fast"} models`} · <span className={st.cls}>{statusText}</span>
        </Meta>
        <Headline>{headline.map((l, i) => <span key={i}>{l}{i < headline.length - 1 && <br />}</span>)}</Headline>
      </div>

      <Stats items={[
        { value: stats.hs.length || "—", label: stats.hs.length ? `hypotheses · ${stats.survived} survived · ${stats.killed.size} killed` : "hypotheses" },
        { value: stats.kept, of: stats.total, label: "sources hold" },
        { value: stats.flagged, label: "flagged" },
        { value: `$${(run.cost?.total_usd ?? 0).toFixed(2)}`, label: `${run.step_count} steps` },
      ]} />

      <Pipeline run={run} />

      {run.error && <p className="border-l-2 border-bad pl-3 text-[14px] text-bad"><span className="font-semibold">The run stopped: </span>{run.error}</p>}
      <MemoStrip run={run} />

      {run.status === "paused" && <Review key={`${run.run_id}-${run.step_count}`} runId={run.run_id} onDone={load} />}

      <nav className="flex gap-8 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`-mb-px whitespace-nowrap border-b-2 pb-3 text-[13px] font-medium uppercase tracking-[.06em] transition ${tab === t.key ? "border-paper text-paper" : "border-transparent text-dim hover:text-paper"}`}>
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "overview" && (
        <div className="flex flex-col gap-9">
          {result?.intake && <Intake intake={result.intake} />}
          <div className="grid min-w-0 grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0">
              <Label className="border-b border-line pb-3">Hypotheses · {stats.hs.length}</Label>
              {stats.hs.length ? stats.hs.map((h) => (
                <div key={h.id} className="flex items-baseline justify-between gap-4 border-b border-line py-3">
                  <span className="min-w-0"><span className="block truncate text-[15px]">{h.mechanism}</span><span className="text-[13px] text-dim">{h.id} · {h.validation_level || "—"} · tier {h.evidence_tier ?? "—"}</span></span>
                  <span className={`shrink-0 text-[13px] font-semibold uppercase tracking-[.06em] ${verdictCls(h.verdict, stats.killed.has(h.id))}`}>
                    {stats.killed.has(h.id) ? "killed" : h.verdict || (run.current_hypothesis === h.id && run.status !== "done" ? "in progress" : pendingLabel(run.status))}
                  </span>
                </div>
              )) : <p className="py-3 text-[14px] text-dim">Ranked after the Mechanisms agent runs.</p>}
            </div>
            <div className="self-start"><Label className="border-b border-line pb-3">Evidence by agent</Label><EvidenceChart citations={run.citations} /></div>
          </div>
        </div>
      )}
      {tab === "hypotheses" && (
        stats.hs.length
          ? <div className="grid grid-cols-1 gap-x-12 md:grid-cols-2">{stats.hs.map((h) => <HypothesisCard key={h.id} h={h} killed={stats.killed.has(h.id)} current={run.current_hypothesis === h.id && run.status !== "done"} runStatus={run.status} result={result} />)}</div>
          : <p className="text-[14px] text-dim">Ranked after the Mechanisms agent runs.</p>
      )}
      {tab === "evidence" && (
        <div className="grid min-w-0 grid-cols-1 gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0">
            <Label className="border-b border-line pb-3">Every claim and its source</Label>
            {Object.keys(run.citations || {}).length === 0
              ? <p className="py-3 text-[14px] text-dim">Citation checks appear as each agent finishes.</p>
              : Object.entries(run.citations).map(([k, r]) => <Ledger key={k} name={agentName(k.split(":")[0]) + (k.split(":")[1] ? ` · ${k.split(":")[1]}` : "")} report={r} />)}
          </div>
          <div className="self-start"><Label className="border-b border-line pb-3">By agent</Label><EvidenceChart citations={run.citations} /></div>
        </div>
      )}
      {tab === "report" && <Report runId={run.run_id} done={run.status === "done"} />}
      {tab === "activity" && (
        <div>
          <Label className="border-b border-line pb-3">Activity · {run.step_count} steps</Label>
          <ol className="max-h-[32rem] overflow-auto">
            {[...(run.steps || [])].reverse().map((step, i) => {
              const cite = /_cite$/.test(step.node || "") || step.node === "a7_final"; // the A7 citation check
              return (
                <li key={i} className="flex gap-4 border-b border-line py-2 text-[14px]">
                  <span className={`w-40 shrink-0 font-mono text-[12px] leading-5 ${cite ? "text-paper" : "text-dim"}`}>{step.node}{step.hid ? ` · ${step.hid}` : ""}</span>
                  <span className={cite ? "text-paper" : "text-paper/80"}>{step.detail}</span>
                </li>
              );
            })}
          </ol>
        </div>
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

  return (
    <div className="flex min-h-screen flex-col bg-ink text-paper">
      <header className="flex h-16 items-center border-b border-line px-4 sm:px-12"><Wordmark /></header>
      <div className="grid flex-1 grid-cols-1 gap-12 px-4 py-10 sm:px-12 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div>
          <Meta>UPMC Enterprises · evidence-checked biotech investment theses</Meta>
          <Headline>Auctor.<br />Sign in.</Headline>
          <p className="mt-6 max-w-xl text-[15px] leading-[1.45] text-dim">One disease area in, two or three evidence-checked pursuits out. Every claim is cited, and every citation is checked against its source.</p>
        </div>
        <div className="flex flex-col gap-3 self-start lg:pt-8">
          <BtnPrimary onClick={() => base44.auth.loginWithProvider("google", window.location.origin + "/")}>Continue with Google</BtnPrimary>
          <div className="my-3 flex items-center gap-3 text-[11px] uppercase tracking-[.14em] text-dim"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
          <form onSubmit={withPassword} className="flex flex-col gap-4">
            <label className={fieldLabel}>Email<input type="email" className={field} value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
            <label className={fieldLabel}>Password<input type="password" className={field} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
            {err && <p className="border-l-2 border-bad pl-3 text-[13px] text-bad">{err}</p>}
            <BtnLine type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</BtnLine>
          </form>
          <p className="mt-2 text-[13px] leading-[1.45] text-dim">Only signed-in users can start or review analyses.</p>
        </div>
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
    document.title = "Auctor · Evidence-checked biotech investment theses";
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

  if (authState === "checking") return <div className="flex h-screen items-center justify-center bg-ink"><Loader2 className="h-6 w-6 animate-spin text-dim" /></div>;
  if (authState === "out") return <SignIn />;

  const navCls = (on) => `text-[13px] font-medium uppercase tracking-[.06em] ${on ? "text-paper" : "text-dim hover:text-paper"}`;
  return (
    <div className="flex min-h-screen flex-col bg-ink text-paper">
      <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-line bg-ink px-4 sm:px-12">
        <button onClick={() => setSelected(null)}><Wordmark /></button>
        <nav className="flex items-center gap-5 sm:gap-8">
          <button className={navCls(!!selected)} onClick={() => runs[0] && setSelected(selected || runs[runs.length - 1].run_id)}>Analyses</button>
          <button className={navCls(!selected)} onClick={() => setSelected(null)}>New</button>
          <span className="hidden text-[13px] uppercase tracking-[.06em] text-dim md:inline">{user.email}</span>
          <button className={navCls(false)} title="Sign out" onClick={() => base44.auth.logout(window.location.origin + "/")}>Sign out</button>
        </nav>
      </header>

      <div className="grid flex-1 grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="border-b border-line px-4 pb-6 pt-10 sm:px-12 lg:border-b-0 lg:border-r lg:pl-12 lg:pr-0">
          <Label className="pb-4">Analyses</Label>
          {err && <p className="pb-3 pr-6 text-[13px] text-bad">{err}</p>}
          {runs.length === 0 && !err && <p className="border-t border-line py-3.5 pr-6 text-[14px] text-dim">No analyses yet.</p>}
          <div className="max-h-[40vh] overflow-auto lg:max-h-none">
            {[...runs].reverse().map((r, i, arr) => {
              const rs = statusOf(r.status);
              const on = selected === r.run_id;
              return (
                <button key={r.run_id} onClick={() => setSelected(r.run_id)}
                  className={`block w-full border-t border-line py-3.5 pr-6 text-left text-[15px] leading-[1.3] ${i === arr.length - 1 ? "border-b" : ""} ${on ? "text-paper" : "text-dim hover:text-paper"}`}>
                  <span className="flex justify-between gap-3"><span className={on ? "font-semibold" : "text-paper"}>{r.settings?.disease || "Analysis"}</span><span className={rs.cls}>{rs.label}</span></span>
                  <span className="mt-0.5 block text-[13px] text-dim">{r.run_id} · {(r.started || "").slice(11, 16)}{r.settings?.llm === "stub" ? " · test" : ""}</span>
                </button>
              );
            })}
          </div>
        </aside>
        <main className="min-w-0 px-4 pb-16 pt-10 sm:px-12">{selected ? <RunView runId={selected} /> : <NewRun onStarted={setSelected} />}</main>
      </div>
    </div>
  );
}
