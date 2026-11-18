"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { api, errMsg } from "@/lib/api";

interface MRow {
  name: string;
  planned_start: string;
  planned_end: string;
  budget: string;
  is_critical: boolean;
  depends_on_prev: boolean;
}
interface CRow {
  name: string;
  trade: string;
}

const input = "mt-1 w-full rounded border border-rule px-3 py-2 text-sm outline-none focus:border-ink/50";

export default function NewProject() {
  const router = useRouter();
  const [f, setF] = useState({
    name: "",
    scope: "",
    location: "",
    owner_name: "",
    currency: "INR",
    budget: "",
    start_date: "",
    target_end_date: "",
  });
  const [ms, setMs] = useState<MRow[]>([{ name: "", planned_start: "", planned_end: "", budget: "", is_critical: true, depends_on_prev: false }]);
  const [cs, setCs] = useState<CRow[]>([{ name: "", trade: "" }]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value });

  async function submit() {
    setErr("");
    if (!f.name || !f.budget || !f.start_date || !f.target_end_date) {
      setErr("Name, budget, start and target completion are required.");
      return;
    }
    setBusy(true);
    try {
      const milestones = ms
        .filter((m) => m.name && m.planned_start && m.planned_end)
        .map((m, i) => ({
          name: m.name,
          planned_start: m.planned_start,
          planned_end: m.planned_end,
          budget: Number(m.budget || 0),
          is_critical: m.is_critical,
          depends_on_index: m.depends_on_prev && i > 0 ? [i - 1] : [],
        }));
      const { id } = await api.createProject({
        ...f,
        owner_name: f.owner_name || "Owner",
        budget: Number(f.budget),
        milestones,
        contractors: cs.filter((c) => c.name),
      });
      router.push(`/projects/${id}`);
    } catch (e) {
      setErr(errMsg(e));
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-5 py-8">
      <Link href="/" className="flex items-center gap-1 text-sm text-graphite/70 hover:text-ink">
        <ArrowLeft size={14} /> Back
      </Link>
      <h1 className="mt-4 text-xl font-semibold">New project</h1>
      <p className="text-sm text-graphite/70">The manager agent tracks progress against this baseline: scope, budget, timeline and milestones.</p>

      <div className="mt-5 space-y-4 rounded-md border border-rule bg-panel p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="text-sm font-medium">Project name</span>
            <input className={input} value={f.name} onChange={set("name")} />
          </label>
          <label className="block sm:col-span-2">
            <span className="text-sm font-medium">Scope</span>
            <textarea className={input} rows={3} value={f.scope} onChange={set("scope")} placeholder="What is being built, key systems, contract model…" />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Location</span>
            <input className={input} value={f.location} onChange={set("location")} />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Owner / client</span>
            <input className={input} value={f.owner_name} onChange={set("owner_name")} />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Budget</span>
            <div className="flex gap-2">
              <select className={`${input} w-24`} value={f.currency} onChange={set("currency")}>
                {["INR", "USD", "EUR", "GBP", "AED"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <input className={input} type="number" value={f.budget} onChange={set("budget")} placeholder="e.g. 250000000" />
            </div>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-sm font-medium">Start</span>
              <input className={input} type="date" value={f.start_date} onChange={set("start_date")} />
            </label>
            <label className="block">
              <span className="text-sm font-medium">Target completion</span>
              <input className={input} type="date" value={f.target_end_date} onChange={set("target_end_date")} />
            </label>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Milestones / work packages</span>
            <button
              onClick={() => setMs([...ms, { name: "", planned_start: "", planned_end: "", budget: "", is_critical: true, depends_on_prev: true }])}
              className="flex items-center gap-1 text-xs font-medium text-graphite/80"
            >
              <Plus size={12} /> Add
            </button>
          </div>
          <div className="mt-2 space-y-2">
            {ms.map((m, i) => (
              <div key={i} className="grid grid-cols-12 items-center gap-2">
                <input className={`${input} col-span-4 mt-0`} placeholder="Name" value={m.name} onChange={(e) => setMs(ms.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <input className={`${input} col-span-2 mt-0`} type="date" value={m.planned_start} onChange={(e) => setMs(ms.map((x, j) => (j === i ? { ...x, planned_start: e.target.value } : x)))} />
                <input className={`${input} col-span-2 mt-0`} type="date" value={m.planned_end} onChange={(e) => setMs(ms.map((x, j) => (j === i ? { ...x, planned_end: e.target.value } : x)))} />
                <input className={`${input} col-span-2 mt-0`} type="number" placeholder="Budget" value={m.budget} onChange={(e) => setMs(ms.map((x, j) => (j === i ? { ...x, budget: e.target.value } : x)))} />
                <label className="col-span-1 flex items-center gap-1 text-[11px] text-graphite/70" title="Depends on the previous milestone">
                  <input type="checkbox" disabled={i === 0} checked={m.depends_on_prev} onChange={(e) => setMs(ms.map((x, j) => (j === i ? { ...x, depends_on_prev: e.target.checked } : x)))} />
                  after prev
                </label>
                <button className="col-span-1 text-graphite/50 hover:text-brick" onClick={() => setMs(ms.filter((_, j) => j !== i))} aria-label="Remove milestone">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Contractors</span>
            <button onClick={() => setCs([...cs, { name: "", trade: "" }])} className="flex items-center gap-1 text-xs font-medium text-graphite/80">
              <Plus size={12} /> Add
            </button>
          </div>
          <div className="mt-2 space-y-2">
            {cs.map((c, i) => (
              <div key={i} className="grid grid-cols-12 gap-2">
                <input className={`${input} col-span-6 mt-0`} placeholder="Company" value={c.name} onChange={(e) => setCs(cs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                <input className={`${input} col-span-5 mt-0`} placeholder="Trade" value={c.trade} onChange={(e) => setCs(cs.map((x, j) => (j === i ? { ...x, trade: e.target.value } : x)))} />
                <button className="col-span-1 text-graphite/50 hover:text-brick" onClick={() => setCs(cs.filter((_, j) => j !== i))} aria-label="Remove contractor">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={submit} disabled={busy} className="rounded bg-ink px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
            Create project
          </button>
          {err && <span className="text-sm text-brick">{err}</span>}
        </div>
      </div>
    </main>
  );
}
