"use client";

import { useState } from "react";
import { AlertTriangle, Check, ShieldCheck, Star, X } from "lucide-react";
import { api, errMsg, money, type ChatMessage, type Option } from "@/lib/api";
import { Markdown } from "./Markdown";
import { SeverityBadge } from "./ui";

export function AlertCard({
  msg,
  open,
  currency,
  onDone,
}: {
  msg: ChatMessage;
  open: boolean;
  currency: string;
  onDone: () => void;
}) {
  const options: Option[] = msg.meta.options ?? [];
  const recommended: number | null = msg.meta.recommended ?? null;
  const [picked, setPicked] = useState<number | null>(recommended);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function submit(idx: number | null) {
    setBusy(true);
    setErr("");
    try {
      await api.decide(msg.meta.alert_id, idx, notes);
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-2xl overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50 px-4 py-2.5">
        <AlertTriangle size={16} className={msg.meta.severity === "critical" ? "text-red-600" : "text-amber-600"} />
        <span className="flex-1 text-sm font-semibold">{msg.meta.title}</span>
        <SeverityBadge severity={msg.meta.severity ?? "warning"} />
      </div>
      <div className="px-4 py-3">
        <Markdown>{msg.content}</Markdown>
        {options.length > 0 && (
          <div className="mt-3 space-y-2">
            {options.map((o, i) => (
              <button
                key={i}
                disabled={!open || busy}
                onClick={() => setPicked(i)}
                className={`w-full rounded-lg border px-3 py-2.5 text-left transition ${
                  picked === i && open ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200 hover:border-slate-300"
                } ${!open ? "opacity-70" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{o.label}</span>
                  {recommended === i && (
                    <span className="flex items-center gap-0.5 rounded bg-amber-100 px-1.5 text-[11px] font-medium text-amber-800">
                      <Star size={11} /> Recommended
                    </span>
                  )}
                  <span className="ml-auto flex gap-3 text-xs text-slate-500">
                    {o.impact_days != null && (
                      <span className={o.impact_days > 0 ? "text-emerald-700" : ""}>
                        {o.impact_days > 0 ? `−${o.impact_days}d` : `${o.impact_days}d`} delay
                      </span>
                    )}
                    {o.impact_cost != null && <span>{o.impact_cost > 0 ? `+${money(o.impact_cost, currency)}` : "no extra cost"}</span>}
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-slate-600">{o.description}</p>
                {o.risks && <p className="mt-0.5 text-xs text-slate-500">Risks: {o.risks}</p>}
              </button>
            ))}
          </div>
        )}
        {open ? (
          <div className="mt-3 space-y-2">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder={options.length ? "Optional notes or conditions…" : "Your instructions…"}
              className="w-full resize-none rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
            />
            <div className="flex flex-wrap items-center gap-2">
              {options.length > 0 && (
                <button
                  disabled={picked === null || busy}
                  onClick={() => submit(picked)}
                  className="rounded-lg bg-slate-900 px-3.5 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                >
                  {picked !== null ? `Go with “${options[picked].label}”` : "Pick an option"}
                </button>
              )}
              <button
                disabled={!notes.trim() || busy}
                onClick={() => submit(null)}
                className="rounded-lg border border-slate-200 px-3.5 py-1.5 text-sm font-medium disabled:opacity-40"
              >
                {options.length ? "Do something else (use my notes)" : "Send instructions"}
              </button>
              {err && <span className="text-sm text-red-600">{err}</span>}
            </div>
          </div>
        ) : (
          <div className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
            <Check size={14} /> Decided
          </div>
        )}
      </div>
    </div>
  );
}

export function ApprovalCard({ msg, open, onDone }: { msg: ChatMessage; open: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);

  async function decide(approve: boolean) {
    setBusy(true);
    setErr("");
    try {
      await api.decideAction(msg.meta.action_id, approve);
      setOutcome(approve ? "Approved" : "Rejected");
      onDone();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-2xl rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
        <ShieldCheck size={16} /> Approval required · <span className="font-mono text-xs">{msg.meta.action}</span>
      </div>
      <div className="mt-1.5 text-slate-800">
        <Markdown>{msg.content}</Markdown>
      </div>
      {open && !outcome ? (
        <div className="mt-2 flex items-center gap-2">
          <button
            disabled={busy}
            onClick={() => decide(true)}
            className="flex items-center gap-1 rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            <Check size={14} /> Approve
          </button>
          <button
            disabled={busy}
            onClick={() => decide(false)}
            className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium disabled:opacity-40"
          >
            <X size={14} /> Reject
          </button>
          {err && <span className="text-sm text-red-600">{err}</span>}
        </div>
      ) : (
        <div className="mt-2 text-xs text-slate-500">{outcome ?? "Handled"}</div>
      )}
    </div>
  );
}
