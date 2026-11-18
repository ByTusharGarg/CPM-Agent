"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ChevronDown, ChevronRight, FileText, RefreshCw, Square, Trash2, UserPlus } from "lucide-react";
import { api, errMsg, fmtDate, money, ROLE_LABEL, timeAgo, type Member, type MilestoneForecast, type Role } from "@/lib/api";
import type { ProjectState } from "@/lib/useProject";
import { Markdown } from "./Markdown";
import { Section, SeverityBadge, StatusPill, WORKER_LABEL } from "./ui";

type Tab = "overview" | "agents" | "autonomy" | "reports" | "team";

export function RightPanel({ state }: { state: ProjectState }) {
  const [tab, setTab] = useState<Tab>("overview");
  const needsCount = state.needs.alerts.length + state.needs.approvals.length;
  const running = state.work.filter((w) => w.status === "running" || w.status === "queued").length;
  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "overview", label: "Overview", badge: needsCount },
    { id: "agents", label: "Agents", badge: running },
    { id: "autonomy", label: "Rules" },
    { id: "reports", label: "Reports" },
    { id: "team", label: "Team" },
  ];
  return (
    <aside className="hidden w-[380px] shrink-0 flex-col border-l border-rule bg-panel lg:flex">
      <div role="tablist" className="scroll-thin flex shrink-0 gap-0.5 overflow-x-auto border-b border-rule px-2 pt-1.5">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            role="tab"
            aria-selected={tab === t.id}
            className={`flex shrink-0 items-center gap-1 whitespace-nowrap border-b-2 px-2 py-2 font-display text-[15px] ${
              tab === t.id ? "border-ink font-semibold text-ink" : "border-transparent font-medium text-graphite/70 hover:text-ink"
            }`}
          >
            {t.label}
            {!!t.badge && (
              <span className={`rounded-full px-1.5 text-[11px] font-semibold leading-5 ${t.id === "overview" ? "bg-hivis text-ink" : "bg-ink/5 text-ink-soft"}`}>
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto">
        {tab === "overview" && <Overview state={state} />}
        {tab === "agents" && <Agents state={state} />}
        {tab === "autonomy" && <Autonomy state={state} />}
        {tab === "reports" && <Reports state={state} />}
        {tab === "team" && <Team state={state} />}
      </div>
    </aside>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "bad" | "good" | "warn" }) {
  const c = tone === "bad" ? "text-brick" : tone === "warn" ? "text-[#7a5a00]" : tone === "good" ? "text-site" : "text-ink";
  return (
    <div className="rounded border border-rule px-3 py-2">
      <div className="text-[11px] text-graphite/70">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${c}`}>{value}</div>
      {sub && <div className="text-[11px] text-graphite/70">{sub}</div>}
    </div>
  );
}

function Overview({ state }: { state: ProjectState }) {
  const h = state.health;
  const cur = state.project?.currency ?? "INR";
  const [checking, setChecking] = useState(false);
  const [note, setNote] = useState("");

  async function check() {
    if (!state.project) return;
    setChecking(true);
    try {
      const r = await api.healthCheck(state.project.id);
      setNote(r.new_hits ? `${r.new_hits} new issue(s) sent to the manager` : "No new issues");
    } catch (e) {
      setNote(errMsg(e));
    } finally {
      setChecking(false);
    }
  }

  if (!h) return <p className="p-4 text-sm text-graphite/70">Loading…</p>;
  const spiTone = h.spi == null ? undefined : h.spi < 0.9 ? "bad" : h.spi < 0.97 ? "warn" : "good";
  const cpiTone = h.cpi == null ? undefined : h.cpi < 0.95 ? "bad" : h.cpi < 1 ? "warn" : "good";
  return (
    <>
      <Section
        title={`Health on ${fmtDate(h.as_of)}`}
        right={
          state.can("health.check") && <button onClick={check} disabled={checking} className="flex items-center gap-1 text-xs font-medium text-graphite/80 hover:text-ink">
            <RefreshCw size={12} className={checking ? "animate-spin" : ""} /> Run check
          </button>
        }
      >
        <div className="grid grid-cols-2 gap-2">
          <Kpi label="Progress (actual / planned)" value={`${h.actual_pct.toFixed(0)}% / ${h.planned_pct.toFixed(0)}%`} tone={h.actual_pct + 3 < h.planned_pct ? "bad" : undefined} />
          <Kpi label="Forecast finish" value={fmtDate(h.forecast_end)} sub={h.project_slip_days > 0 ? `${h.project_slip_days} days late` : "on time"} tone={h.project_slip_days > 0 ? "bad" : "good"} />
          <Kpi label="SPI (schedule)" value={h.spi?.toFixed(2) ?? "—"} tone={spiTone} />
          <Kpi label="CPI (cost)" value={h.cpi?.toFixed(2) ?? "—"} tone={cpiTone} />
          <Kpi label="Spent to date" value={money(h.actual_cost, cur)} sub={`of ${money(h.budget, cur)}`} />
          <Kpi label="Forecast at completion" value={money(h.eac, cur)} sub={`${h.cost_variance_pct > 0 ? "+" : ""}${h.cost_variance_pct.toFixed(1)}% vs budget`} tone={h.cost_variance_pct > 5 ? "bad" : h.cost_variance_pct > 0 ? "warn" : "good"} />
        </div>
        {note && <p className="mt-2 text-xs text-graphite/70">{note}</p>}
      </Section>

      <Section title="Needs you">
        {state.needs.alerts.length + state.needs.approvals.length === 0 ? (
          <p className="text-sm text-graphite/70">Nothing needs your attention right now.</p>
        ) : (
          <ul className="space-y-2">
            {state.needs.alerts.map((a) => (
              <li key={`a${a.id}`} className="rounded border border-rule px-3 py-2">
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={a.severity} />
                  <span className="truncate text-sm font-medium">{a.title}</span>
                </div>
                <p className="mt-0.5 text-xs text-graphite/70">{a.options.length} options in the chat</p>
              </li>
            ))}
            {state.needs.approvals.map((x) => (
              <li key={`x${x.id}`} className="rounded border border-hivis bg-hivis-soft px-3 py-2">
                <div className="text-sm font-medium">Approve: {x.summary}</div>
                {!state.can(x.locked ? "action.approve_locked" : "action.approve") ? (
                  <p className="mt-0.5 text-xs text-graphite/70">Waiting for {x.locked ? "the owner" : "the owner or owner's rep"}</p>
                ) : (
                <div className="mt-1 flex gap-2">
                  <button onClick={async () => { await api.decideAction(x.id, true); state.refreshNeeds(); }} className="rounded bg-ink px-2 py-0.5 text-xs text-white">
                    Approve
                  </button>
                  <button onClick={async () => { await api.decideAction(x.id, false); state.refreshNeeds(); }} className="rounded border border-rule bg-panel px-2 py-0.5 text-xs">
                    Reject
                  </button>
                </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Schedule (planned vs forecast)">
        <Timeline items={h.milestones} start={state.project?.start_date} />
      </Section>

      <Section title={`Engine findings (${h.rule_hits.length})`}>
        <ul className="space-y-1.5">
          {h.rule_hits.map((r) => (
            <li key={r.rule_key} className="flex items-start gap-2 text-sm">
              <SeverityBadge severity={r.severity} />
              <span className="text-graphite">{r.title}</span>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}

function Timeline({ items, start }: { items: MilestoneForecast[]; start?: string }) {
  if (!items.length) return <p className="text-sm text-graphite/70">No milestones.</p>;
  const t = (s: string) => new Date(`${s}T00:00:00`).getTime();
  const min = Math.min(...items.map((m) => t(m.planned_start)), start ? t(start) : Infinity);
  const max = Math.max(...items.map((m) => Math.max(t(m.planned_end), t(m.forecast_end))));
  const pct = (v: number) => ((v - min) / (max - min || 1)) * 100;
  const today = Date.now();
  return (
    <div className="space-y-2.5">
      {items.map((m) => {
        const ps = pct(t(m.planned_start)), pe = pct(t(m.planned_end));
        const fs = pct(t(m.forecast_start)), fe = pct(t(m.forecast_end));
        return (
          <div key={m.id}>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate font-medium text-graphite">{m.name}</span>
              <span className={`shrink-0 tabular-nums ${m.status === "done" ? "text-graphite/50" : m.slip_days > 0 ? "text-brick" : "text-site"}`}>
                {m.status === "done" ? "done" : m.slip_days > 0 ? `+${m.slip_days}d` : "on time"}
              </span>
            </div>
            <div className="relative mt-1 h-3 rounded bg-concrete">
              <div className="absolute top-0 h-1.5 rounded-sm bg-rule" style={{ left: `${ps}%`, width: `${Math.max(pe - ps, 0.8)}%` }} title={`Planned ${m.planned_start} → ${m.planned_end}`} />
              <div
                className={`absolute bottom-0 h-1.5 rounded-sm ${m.status === "done" ? "bg-rule" : m.slip_days > 0 ? "bg-brick" : "bg-site"}`}
                style={{ left: `${fs}%`, width: `${Math.max(fe - fs, 0.8)}%` }}
                title={`Forecast ${m.forecast_start} → ${m.forecast_end} · ${m.actual_pct}% done`}
              />
              {today > min && today < max && <div className="absolute -top-0.5 h-4 w-px bg-ink/60" style={{ left: `${pct(today)}%` }} />}
            </div>
          </div>
        );
      })}
      <div className="flex gap-3 pt-1 text-[11px] text-graphite/70">
        <span className="flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-sm bg-rule" /> planned</span>
        <span className="flex items-center gap-1"><span className="inline-block h-1.5 w-3 rounded-sm bg-brick" /> forecast (late)</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-px bg-ink/60" /> today</span>
      </div>
    </div>
  );
}

function Agents({ state }: { state: ProjectState }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <Section title="Live activity">
        {state.routerBusy && <p className="mb-1 text-sm text-ink-soft">Project manager is working…</p>}
        {state.activity.length === 0 && !state.routerBusy ? (
          <p className="text-sm text-graphite/70">Idle. Agents wake up on your messages, contractor updates and the daily check.</p>
        ) : (
          <ul className="space-y-1">
            {state.activity.slice().reverse().map((a) => (
              <li key={a.key} className="flex items-center gap-1.5 text-xs text-graphite/80">
                <Activity size={12} className="text-ink-soft" /> <b className="font-medium">{a.actor}</b> {a.tool}
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Work items">
        {state.work.length === 0 ? (
          <p className="text-sm text-graphite/70">No work dispatched yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {state.work.map((w) => (
              <li key={w.id} className="rounded border border-rule">
                <button onClick={() => setOpen(open === w.id ? null : w.id)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
                  {open === w.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{w.title}</div>
                    <div className="text-[11px] text-graphite/70">
                      {WORKER_LABEL[w.worker] ?? w.worker}, {w.turns_used} steps, {timeAgo(w.updated_at)}
                    </div>
                  </div>
                  <StatusPill status={w.status} />
                </button>
                {open === w.id && (
                  <div className="border-t border-rule px-3 py-2">
                    {w.result ? <Markdown>{w.result}</Markdown> : <p className="text-xs text-graphite/70">No report yet.</p>}
                    {(w.status === "running" || w.status === "queued") && state.can("work.cancel") && (
                      <button
                        onClick={async () => { await api.cancelWork(w.id); state.refreshWork(); }}
                        className="mt-2 flex items-center gap-1 rounded border border-rule px-2 py-1 text-xs"
                      >
                        <Square size={10} /> Stop
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

const MODES = ["off", "observe", "auto"] as const;

function Autonomy({ state }: { state: ProjectState }) {
  const pid = state.project?.id;
  const observed = state.observed.filter((a) => a.status === "observed");
  return (
    <>
      <Section title="What agents may do on their own">
        <p className="mb-2 text-xs text-graphite/70">
          <b>Auto</b> means the agent does it, <b>observe</b> means it only logs what it would have done, <b>off</b> means never. Baseline schedule and budget changes always need your approval.
        </p>
        <ul className="space-y-1.5">
          {state.policies.map((p) => (
            <li key={p.action} className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-display text-xs">{p.action}</div>
                <div className="truncate text-[11px] text-graphite/70">{p.description}</div>
              </div>
              {p.locked ? (
                <span className="rounded bg-hivis-soft px-1.5 py-0.5 text-[11px] font-medium text-[#7a5a00]">approval</span>
              ) : !state.can("policy.edit") ? (
                <span className="rounded bg-concrete px-1.5 py-0.5 text-[11px] font-medium text-graphite/80">{p.mode}</span>
              ) : (
                <div className="flex overflow-hidden rounded-md border border-rule">
                  {MODES.map((m) => (
                    <button
                      key={m}
                      onClick={async () => { if (pid) { await api.setPolicy(pid, p.action, m); state.refreshObserved(); } }}
                      className={`px-1.5 py-0.5 text-[11px] ${p.mode === m ? "bg-ink text-white" : "bg-panel text-graphite/80 hover:bg-concrete"}`}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Section>
      <Section title={`Observe log (${observed.length})`}>
        {observed.length === 0 ? (
          <p className="text-sm text-graphite/70">Nothing logged in observe mode yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {observed.map((a) => (
              <li key={a.id} className="rounded border border-rule bg-concrete/60 px-3 py-2 text-sm">
                <div className="font-medium">Would have: {a.summary}</div>
                <div className="text-[11px] text-graphite/70">
                  {a.actor}, {timeAgo(a.created_at)}{a.reason && `: ${a.reason}`}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Audit trail">
        <ul className="space-y-1">
          {state.observed.slice(0, 25).map((a) => (
            <li key={a.id} className="flex items-center gap-2 text-xs">
              <StatusPill status={a.status} />
              <span className="truncate text-graphite">{a.summary}</span>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}

function Reports({ state }: { state: ProjectState }) {
  const [msg, setMsg] = useState("");
  return (
    <Section
      title="Owner reports"
      right={
        state.can("report.request") && <button
          onClick={async () => {
            if (!state.project) return;
            await api.requestReport(state.project.id);
            setMsg("Requested - the reporter agent is writing it.");
          }}
          className="text-xs font-medium text-graphite/80 hover:text-ink"
        >
          Generate now
        </button>
      }
    >
      {msg && <p className="mb-2 text-xs text-graphite/70">{msg}</p>}
      {state.reports.length === 0 ? (
        <p className="text-sm text-graphite/70">Weekly reports appear here (Mondays by default).</p>
      ) : (
        <ul className="space-y-1.5">
          {state.reports.map((r) => (
            <li key={r.id}>
              <Link href={`/reports/${r.id}`} className="flex items-center gap-2 rounded border border-rule px-3 py-2 text-sm hover:bg-concrete">
                <FileText size={14} /> <span className="flex-1 font-medium">{r.period}</span>
                <span className="text-xs text-graphite/70">{timeAgo(r.created_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

const ROLES: Role[] = ["owner", "owner_rep", "consultant", "contractor", "viewer"];

function Team({ state }: { state: ProjectState }) {
  const pid = state.project?.id;
  const canManage = state.can("members.manage");
  const [members, setMembers] = useState<Member[]>([]);
  const [form, setForm] = useState({ email: "", name: "", phone: "", role: "viewer" as Role, contractor_id: "" });
  const [msg, setMsg] = useState("");
  const load = () => {
    if (pid) api.members(pid).then(setMembers).catch((e) => setMsg(errMsg(e)));
  };
  useEffect(load, [pid]);

  async function add() {
    if (!pid) return;
    setMsg("");
    try {
      await api.addMember(pid, {
        email: form.email, name: form.name, phone: form.phone, role: form.role,
        contractor_id: form.role === "contractor" && form.contractor_id ? Number(form.contractor_id) : null,
      });
      setForm({ ...form, email: "", name: "", phone: "" });
      load();
    } catch (e) {
      setMsg(errMsg(e));
    }
  }

  async function remove(id: number) {
    if (!pid) return;
    try {
      await api.removeMember(pid, id);
    } catch (e) {
      setMsg(errMsg(e));
    }
    load();
  }

  const contractorName = (id: number | null) => state.project?.contractors.find((c) => c.id === id)?.name;
  const input = "w-full rounded-md border border-rule px-2 py-1 text-sm";
  return (
    <>
      <Section title={`Members (${members.length})`}>
        <ul className="space-y-1.5">
          {members.map((m) => (
            <li key={m.id} className="flex items-center gap-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{m.name || m.email}</div>
                <div className="truncate text-[11px] text-graphite/70">
                  {m.email}
                  {m.contractor_id ? `, ${contractorName(m.contractor_id) ?? "contractor"}` : ""}
                </div>
              </div>
              <span className="rounded bg-concrete px-1.5 py-0.5 text-[11px] font-medium text-graphite">{ROLE_LABEL[m.role]}</span>
              {canManage && (
                <button aria-label="Remove member" onClick={() => remove(m.id)} className="text-graphite/50 hover:text-brick">
                  <Trash2 size={13} />
                </button>
              )}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11px] text-graphite/70">Organization admins have owner access to every project.</p>
      </Section>
      {canManage && (
        <Section title="Add member">
          <div className="space-y-2">
            <input className={input} placeholder="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            <div className="flex gap-2">
              <input className={input} placeholder="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <input className={input} placeholder="phone (+91...)" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </div>
            <div className="flex gap-2">
              <select className={input} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                ))}
              </select>
              {form.role === "contractor" && (
                <select className={input} value={form.contractor_id} onChange={(e) => setForm({ ...form, contractor_id: e.target.value })}>
                  <option value="">company...</option>
                  {state.project?.contractors.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              )}
            </div>
            <button onClick={add} disabled={!form.email} className="flex items-center gap-1 rounded bg-ink px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
              <UserPlus size={14} /> Add
            </button>
            {msg && <p className="text-xs text-brick">{msg}</p>}
            <p className="text-[11px] text-graphite/70">They sign in with a one-time code sent to this email.</p>
          </div>
        </Section>
      )}
    </>
  );
}
