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
    <div className={`w-full max-w-2xl overflow-hidden rounded-md border border-rule bg-panel ${open ? "border-l-[5px] border-l-hivis" : ""}`}>
      <div className="flex items-center gap-2 border-b border-rule bg-concrete/60 px-4 py-2.5">
        <AlertTriangle size={16} className={msg.meta.severity === "critical" ? "text-brick" : "text-[#7a5a00]"} />
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
                className={`w-full rounded border px-3 py-2.5 text-left transition ${
                  picked === i && open ? "border-ink ring-1 ring-ink" : "border-rule hover:border-rule"
                } ${!open ? "opacity-70" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold">{o.label}</span>
                  {recommended === i && (
                    <span className="flex items-center gap-0.5 rounded bg-hivis-soft px-1.5 text-[11px] font-medium text-[#7a5a00]">
                      <Star size={11} /> Recommended
                    </span>
                  )}
                  <span className="ml-auto flex gap-3 text-xs text-graphite/70">
                    {o.impact_days != null && (
                      <span className={o.impact_days > 0 ? "text-site" : ""}>
                        {o.impact_days > 0 ? `−${o.impact_days}d` : `${o.impact_days}d`} delay
                      </span>
                    )}
                    {o.impact_cost != null && <span>{o.impact_cost > 0 ? `+${money(o.impact_cost, currency)}` : "no extra cost"}</span>}
                  </span>
                </div>
                <p className="mt-0.5 text-sm text-graphite/80">{o.description}</p>
                {o.risks && <p className="mt-0.5 text-xs text-graphite/70">Risks: {o.risks}</p>}
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
              className="w-full resize-none rounded border border-rule px-3 py-2 text-sm outline-none focus:border-ink/50"
            />
            <div className="flex flex-wrap items-center gap-2">
              {options.length > 0 && (
                <button
                  disabled={picked === null || busy}
                  onClick={() => submit(picked)}
                  className="rounded bg-ink px-3.5 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                >
                  {picked !== null ? `Go with “${options[picked].label}”` : "Pick an option"}
                </button>
              )}
              <button
                disabled={!notes.trim() || busy}
                onClick={() => submit(null)}
                className="rounded border border-rule px-3.5 py-1.5 text-sm font-medium disabled:opacity-40"
              >
                {options.length ? "Do something else (use my notes)" : "Send instructions"}
              </button>
              {err && <span className="text-sm text-brick">{err}</span>}
            </div>
          </div>
        ) : (
          <div className="mt-3 flex items-center gap-1.5 text-xs text-graphite/70">
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
    <div className="w-full max-w-2xl rounded-md border border-hivis bg-hivis-soft px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-sm font-semibold text-[#7a5a00]">
        <ShieldCheck size={16} /> Your approval is needed
      </div>
      <div className="mt-1.5 text-graphite">
        <Markdown>{msg.content}</Markdown>
      </div>
      {open && !outcome ? (
        <div className="mt-2 flex items-center gap-2">
          <button
            disabled={busy}
            onClick={() => decide(true)}
            className="flex items-center gap-1 rounded bg-ink px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            <Check size={14} /> Approve
          </button>
          <button
            disabled={busy}
            onClick={() => decide(false)}
            className="flex items-center gap-1 rounded border border-rule bg-panel px-3 py-1.5 text-sm font-medium disabled:opacity-40"
          >
            <X size={14} /> Reject
          </button>
          {err && <span className="text-sm text-brick">{err}</span>}
        </div>
      ) : (
        <div className="mt-2 text-xs text-graphite/70">{outcome ?? "Handled"}</div>
      )}
    </div>
  );
}
