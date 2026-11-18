"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { Upload } from "lucide-react";
import { SiteBoard } from "@/components/SiteBoard";
import { useHeader } from "@/lib/useHeader";
import { api, errMsg, fmtDate, type Me, type Schedule, type ScheduleActivity } from "@/lib/api";

const DAY = 86_400_000;
const PX_PER_DAY = 3;
const t = (s: string) => new Date(`${s}T00:00:00`).getTime();

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const [sched, setSched] = useState<Schedule | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState<"gantt" | "lookahead">("gantt");
  const [weeks, setWeeks] = useState(3);
  const [look, setLook] = useState<ScheduleActivity[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const header = useHeader(pid);

  const load = () => api.schedule(pid).then(setSched).catch((e) => setMsg({ ok: false, text: errMsg(e) }));
  useEffect(() => {
    load();
    api.me().then(setMe).catch(() => {});
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (tab === "lookahead") api.lookahead(pid, weeks).then((r) => setLook(r.activities)).catch(() => {});
  }, [tab, weeks, pid]);

  const canImport = !!me?.projects.find((p) => p.id === pid)?.permissions.includes("schedule.import");

  async function onImport(f: File | undefined) {
    if (!f) return;
    if (!confirm(`Import ${f.name}? This replaces the activity network and sets a new current baseline.`)) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.importSchedule(pid, f);
      setMsg({ ok: true, text: `Imported ${r.activities} activities, ${r.links} links into ${r.milestones} milestones.` });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: errMsg(e) });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const currentBaseline = sched?.baselines.find((b) => b.current);
  const minFloat = sched ? Math.min(0, ...sched.activities.filter((a) => a.status !== "done").map((a) => a.total_float)) : 0;
  const late = sched ? Math.round((t(sched.forecast_finish) - t(sched.target_finish)) / DAY) : 0;

  return (
    <main className="flex h-screen flex-col">
      <SiteBoard project={header.project} health={header.health} can={header.can} />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule bg-panel px-5 py-2.5">
        {sched && (
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[13px] text-graphite/80">
            <span>Progress measured to <b>{fmtDate(sched.data_date)}</b></span>
            {late > 0 && <span className="text-brick">Finishes {late} days after target</span>}
            {sched.has_network && <span>Worst float <b className={minFloat < 0 ? "text-brick" : ""}>{minFloat} working days</b></span>}
            {currentBaseline && <span>Compared with <b>{currentBaseline.name}</b></span>}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          {canImport && (
            <>
              <input ref={fileRef} type="file" accept=".xml,.xer" className="hidden" onChange={(e) => onImport(e.target.files?.[0])} />
              <button
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="flex items-center gap-1.5 rounded border border-rule px-3 py-1.5 text-sm font-medium hover:bg-concrete disabled:opacity-40"
              >
                <Upload size={14} /> {busy ? "Importing…" : "Import MS Project / P6"}
              </button>
            </>
          )}
        </div>
      </div>
      {msg && <p className={`px-5 py-2 text-sm ${msg.ok ? "text-site" : "text-brick"}`}>{msg.text}</p>}

      {sched && !sched.has_network ? (
        <div className="p-8 text-sm text-graphite/80">
          This project is tracked at milestone level only.{" "}
          {canImport ? "Import an MS Project XML or Primavera P6 XER file to get critical-path scheduling." : "Ask the owner to import an MS Project or P6 schedule for critical-path scheduling."}
        </div>
      ) : (
        <>
          <div className="flex gap-1 border-b border-rule bg-panel px-4">
            {(["gantt", "lookahead"] as const).map((k) => (
              <button
                key={k}
                onClick={() => setTab(k)}
                className={`px-3 py-2 text-sm ${tab === k ? "border-b-2 border-ink font-semibold" : "text-graphite/70 hover:text-ink"}`}
              >
                {k === "gantt" ? "Gantt" : "Lookahead"}
              </button>
            ))}
          </div>
          {sched && tab === "gantt" && <Gantt sched={sched} />}
          {tab === "lookahead" && (
            <div className="scroll-thin flex-1 overflow-auto p-5">
              <div className="mb-3 flex items-center gap-2 text-sm">
                Next
                <select value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} className="rounded-md border border-rule px-2 py-1">
                  {[2, 3, 4, 6, 8].map((w) => (
                    <option key={w} value={w}>{w} weeks</option>
                  ))}
                </select>
                <span className="text-graphite/70">— work in progress or due to start</span>
              </div>
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className="border-b border-rule text-left text-xs text-graphite/70">
                    <th className="py-2">Activity</th><th>Status</th><th>Start</th><th>Finish</th><th>Float</th><th>Waiting on</th>
                  </tr>
                </thead>
                <tbody>
                  {look.map((a) => (
                    <tr key={a.id} className="border-b border-rule">
                      <td className="py-2 pr-3">
                        <span className={a.critical ? "font-semibold text-brick" : ""}>{a.name}</span>
                        {a.critical && <span className="ml-1.5 rounded bg-brick/10 px-1 text-[10px] font-semibold text-brick">Critical path</span>}
                      </td>
                      <td className="pr-3">{a.status === "in_progress" ? `${a.pct.toFixed(0)}% done` : "to start"}</td>
                      <td className="pr-3">{fmtDate(a.early_start)}</td>
                      <td className="pr-3">{fmtDate(a.early_finish)}</td>
                      <td className={`pr-3 tabular-nums ${a.total_float < 0 ? "text-brick" : ""}`}>{a.total_float}d</td>
                      <td className="text-xs text-graphite/80">{driverLabel(a.driver, sched)}</td>
                    </tr>
                  ))}
                  {look.length === 0 && (
                    <tr><td colSpan={6} className="py-6 text-center text-graphite/70">Nothing in this window.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </main>
  );
}

function driverLabel(driver: string, sched: Schedule | null): string {
  if (!driver || driver === "data_date") return "ready (data date)";
  if (driver === "actual") return "—";
  const [ref, type] = driver.split(" ");
  const [kind, idStr] = ref.split(":");
  if (kind === "activity") {
    const a = sched?.activities.find((x) => x.id === Number(idStr));
    return `${a?.name ?? ref} (${type})`;
  }
  if (kind === "procurement") return `material delivery (${ref})`;
  if (kind === "design") return `design approval (${ref})`;
  return driver;
}

function Gantt({ sched }: { sched: Schedule }) {
  const { start, end, rows } = useMemo(() => {
    const dates = sched.activities.flatMap((a) => [a.early_start, a.early_finish, a.baseline_start, a.baseline_finish].filter(Boolean) as string[]);
    dates.push(sched.target_finish, sched.data_date);
    const min = Math.min(...dates.map(t)) - 7 * DAY;
    const max = Math.max(...dates.map(t)) + 14 * DAY;
    const byMs = new Map<number | null, ScheduleActivity[]>();
    for (const a of sched.activities) byMs.set(a.milestone_id, [...(byMs.get(a.milestone_id) ?? []), a]);
    const rows: ({ kind: "ms"; name: string; acts: ScheduleActivity[] } | { kind: "act"; a: ScheduleActivity })[] = [];
    for (const m of [...sched.milestones, { id: null as unknown as number, name: "Other" }]) {
      const acts = byMs.get(m.id ?? null);
      if (!acts?.length) continue;
      rows.push({ kind: "ms", name: m.name, acts });
      for (const a of acts) rows.push({ kind: "act", a });
    }
    return { start: min, end: max, rows };
  }, [sched]);

  const width = Math.round((end - start) / DAY) * PX_PER_DAY;
  const x = (s: string) => Math.round((t(s) - start) / DAY) * PX_PER_DAY;
  const w = (s: string, f: string) => Math.max(Math.round((t(f) - t(s)) / DAY + 1) * PX_PER_DAY, 3);
  const months: { label: string; left: number }[] = [];
  const d = new Date(start);
  d.setDate(1);
  while (d.getTime() < end) {
    if (d.getTime() >= start) months.push({ label: d.toLocaleDateString(undefined, { month: "short", year: "2-digit" }), left: Math.round((d.getTime() - start) / DAY) * PX_PER_DAY });
    d.setMonth(d.getMonth() + 1);
  }

  return (
    <div className="scroll-thin flex-1 overflow-auto bg-panel">
      <div className="flex min-w-max">
        <div className="sticky left-0 z-10 w-[340px] shrink-0 border-r border-rule bg-panel">
          <div className="sticky top-0 z-10 flex h-8 items-center border-b border-rule bg-concrete/60 px-3 text-[11px] font-semibold text-graphite/70">
            <span className="flex-1">Activity</span><span className="w-12 text-right">Dur</span><span className="w-14 text-right">Float</span>
          </div>
          {rows.map((r, i) =>
            r.kind === "ms" ? (
              <div key={`m${i}`} className="flex h-7 items-center bg-concrete/60 px-3 text-xs font-semibold text-graphite">{r.name}</div>
            ) : (
              <div key={r.a.id} className="flex h-7 items-center px-3 text-xs" title={`${r.a.name}\nDriver: ${r.a.driver}`}>
                <span className={`flex-1 truncate pl-3 ${r.a.critical ? "font-medium text-brick" : "text-graphite"}`}>{r.a.name}</span>
                <span className="w-12 text-right tabular-nums text-graphite/70">{r.a.duration}d</span>
                <span className={`w-14 text-right tabular-nums ${r.a.status === "done" ? "text-graphite/50" : r.a.total_float < 0 ? "text-brick" : "text-graphite/80"}`}>
                  {r.a.status === "done" ? "done" : `${r.a.total_float}d`}
                </span>
              </div>
            ),
          )}
        </div>
        <div className="relative" style={{ width }}>
          <div className="sticky top-0 z-[5] h-8 border-b border-rule bg-concrete/60">
            {months.map((m) => (
              <div key={m.left} className="absolute top-0 h-8 border-l border-rule pl-1 text-[11px] leading-8 text-graphite/70" style={{ left: m.left }}>
                {m.label}
              </div>
            ))}
          </div>
          <div className="pointer-events-none absolute bottom-0 top-8 z-[4] w-px bg-ink/70" style={{ left: x(sched.data_date) }} title="Data date" />
          <div className="pointer-events-none absolute bottom-0 top-8 z-[4] w-px border-l border-dashed border-hivis" style={{ left: x(sched.target_finish) }} title="Target finish" />
          {rows.map((r, i) =>
            r.kind === "ms" ? (
              <div key={`m${i}`} className="h-7 bg-concrete/60" />
            ) : (
              <div key={r.a.id} className="relative h-7">
                {r.a.baseline_start && r.a.baseline_finish && (
                  <div className="absolute top-1 h-1.5 rounded-sm bg-rule" style={{ left: x(r.a.baseline_start), width: w(r.a.baseline_start, r.a.baseline_finish) }} title={`Baseline ${r.a.baseline_start} → ${r.a.baseline_finish}`} />
                )}
                {r.a.duration === 0 ? (
                  <div className={`absolute top-2.5 h-3 w-3 rotate-45 ${r.a.critical ? "bg-brick" : "bg-ink-soft"}`} style={{ left: x(r.a.early_start) - 6 }} title={`${r.a.name} ${r.a.early_start}`} />
                ) : (
                  <div
                    className={`absolute top-3 h-3 overflow-hidden rounded-sm ${r.a.status === "done" ? "bg-rule" : r.a.critical ? "bg-brick" : "bg-ink-soft"}`}
                    style={{ left: x(r.a.early_start), width: w(r.a.early_start, r.a.early_finish) }}
                    title={`${r.a.name}\n${r.a.early_start} → ${r.a.early_finish}\nfloat ${r.a.total_float}d · ${r.a.pct}% done`}
                  >
                    {r.a.pct > 0 && r.a.status !== "done" && <div className="h-full bg-black/25" style={{ width: `${r.a.pct}%` }} />}
                  </div>
                )}
              </div>
            ),
          )}
        </div>
      </div>
      <div className="sticky left-0 flex gap-4 border-t border-rule bg-panel px-4 py-2 text-[11px] text-graphite/70">
        <span className="flex items-center gap-1"><span className="inline-block h-1.5 w-4 rounded-sm bg-rule" /> baseline</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-4 rounded-sm bg-brick" /> critical</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-4 rounded-sm bg-ink-soft" /> has float</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-px bg-ink" /> data date</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-px border-l border-dashed border-hivis" /> target finish</span>
      </div>
    </div>
  );
}
