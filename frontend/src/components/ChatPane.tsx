"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUp, FileText, Loader2 } from "lucide-react";
import type { ProjectState } from "@/lib/useProject";
import { errMsg } from "@/lib/api";
import { AlertCard, ApprovalCard } from "./Cards";
import { Markdown } from "./Markdown";
import { SiteBoard } from "./SiteBoard";

const SUGGESTIONS = [
  "How are we doing overall?",
  "What's driving the delay?",
  "Are we within budget?",
  "Which decisions do you need from me?",
];

export function ChatPane({ state }: { state: ProjectState }) {
  const { project, chat, streaming, activity, routerBusy, connected, needs, health } = state;
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.length, streaming?.text, activity.length]);

  const openAlerts = new Set(needs.alerts.map((a) => a.id));
  const openApprovals = new Set(
    needs.approvals.filter((a) => state.can(a.locked ? "action.approve_locked" : "action.approve")).map((a) => a.id),
  );
  const canDecide = state.can("alert.decide");
  const canChat = state.can("chat.send");

  async function submit(t = text) {
    const v = t.trim();
    if (!v || sending) return;
    setSending(true);
    setText("");
    try {
      await state.send(v);
    } catch (e) {
      state.setError(errMsg(e));
      setText(v);
    } finally {
      setSending(false);
    }
  }

  const waiting = routerBusy && !streaming;

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <SiteBoard project={project} health={health} connected={connected} can={state.can} />

      <div className="scroll-thin flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-5 py-6">
          {chat.map((m) => {
            if (m.kind === "alert")
              return (
                <Row key={m.id} role="manager">
                  <AlertCard msg={m} open={openAlerts.has(m.meta.alert_id) && canDecide} currency={project?.currency ?? "INR"} onDone={state.refreshNeeds} />
                </Row>
              );
            if (m.kind === "approval")
              return (
                <Row key={m.id} role="manager">
                  <ApprovalCard msg={m} open={openApprovals.has(m.meta.action_id)} onDone={() => { state.refreshNeeds(); state.refreshObserved(); }} />
                </Row>
              );
            return (
              <Row key={m.id} role={m.role}>
                <Bubble role={m.role}>
                  {m.role === "owner" && (m.meta?.by || (m.meta?.channel && m.meta.channel !== "web")) && (
                    <div className="mb-0.5 text-[11px] text-graphite/50">
                      {m.meta.by}
                      {m.meta.channel && m.meta.channel !== "web" ? ` via ${m.meta.channel === "whatsapp" ? "WhatsApp" : m.meta.channel}` : ""}
                    </div>
                  )}
                  <Markdown>{m.content}</Markdown>
                  {m.meta?.refs?.some((r: string) => r.startsWith("report:")) && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {m.meta.refs
                        .filter((r: string) => r.startsWith("report:"))
                        .map((r: string) => (
                          <Link key={r} href={`/reports/${r.split(":")[1]}`} className="flex items-center gap-1 rounded-md bg-concrete px-2 py-1 text-xs font-medium hover:bg-rule/60">
                            <FileText size={12} /> Open report
                          </Link>
                        ))}
                    </div>
                  )}
                </Bubble>
              </Row>
            );
          })}

          {streaming && (
            <Row role="manager">
              <Bubble role="manager">
                {streaming.text ? <Markdown>{streaming.text}</Markdown> : <Typing />}
              </Bubble>
            </Row>
          )}
          {waiting && (
            <Row role="manager">
              <div className="flex items-center gap-2 rounded-md px-1 py-1 text-sm text-graphite/70">
                <Loader2 size={14} className="animate-spin" /> Working…
              </div>
            </Row>
          )}
          {activity.length > 0 && (
            <div className="ml-11 flex flex-wrap gap-1.5">
              {activity.map((a) => (
                <span key={a.key} className="rounded-full border border-rule bg-panel px-2.5 py-1 text-xs text-graphite/80">
                  <span className="font-medium text-graphite">{a.actor}</span> is {humanTool(a.tool)}
                </span>
              ))}
            </div>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="border-t border-rule bg-panel px-5 py-3">
        <div className="mx-auto max-w-3xl">
          {!canChat && project && (
            <p className="rounded-md bg-concrete/60 px-4 py-3 text-center text-sm text-graphite/70">
              Read-only: your role on this project ({project.my_role.replace("_", " ")}) can follow the conversation but not message the manager.
            </p>
          )}
          {canChat && chat.length <= 2 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => submit(s)} className="rounded-full border border-rule px-3 py-1 text-xs text-graphite/80 hover:bg-concrete">
                  {s}
                </button>
              ))}
            </div>
          )}
          {canChat && (
          <div className="flex items-end gap-2 rounded-md border border-rule bg-panel px-3 py-2 focus-within:border-ink/50">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={1}
              placeholder="Message your project manager…"
              className="max-h-40 min-h-[24px] flex-1 resize-none bg-transparent py-1 text-[0.95rem] outline-none"
            />
            <button
              onClick={() => submit()}
              disabled={!text.trim() || sending}
              aria-label="Send"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-white disabled:opacity-30"
            >
              <ArrowUp size={16} />
            </button>
          </div>
          )}
          {state.error && (
            <p className="mt-1.5 text-xs text-brick">
              {state.error}{" "}
              <button className="underline" onClick={() => state.setError(null)}>
                dismiss
              </button>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ role, children }: { role: string; children: React.ReactNode }) {
  if (role === "owner") return <div className="flex justify-end">{children}</div>;
  return (
    <div className="border-l-2 border-ink/25 pl-4">
      <div className="mb-0.5 font-display text-[14px] font-semibold text-ink">Project manager</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Bubble({ role, children }: { role: string; children: React.ReactNode }) {
  return role === "owner" ? (
    <div className="max-w-[78%] rounded-md bg-ink px-4 py-2.5 text-white [&_.md_a]:text-white [&_.md_code]:bg-panel/15">{children}</div>
  ) : (
    <div className="max-w-[72ch] text-graphite">{children}</div>
  );
}

function Typing() {
  return (
    <span className="typing inline-flex gap-1 py-2">
      <span className="h-1.5 w-1.5 rounded-full bg-rule" />
      <span className="h-1.5 w-1.5 rounded-full bg-rule" />
      <span className="h-1.5 w-1.5 rounded-full bg-rule" />
    </span>
  );
}

const TOOL_LABEL: Record<string, string> = {
  get_health: "checking project health",
  lookup: "looking up records",
  list_records: "reviewing records",
  simulate_schedule: "simulating schedule options",
  record_progress: "recording progress",
  save_parsed_update: "filing daily report",
  remind_contractor: "reminding contractor",
  update_procurement_item: "updating procurement",
  request_expedite: "requesting expedite",
  update_design: "updating design register",
  cost_breakdown: "analysing costs",
  record_cost: "recording cost",
  propose_rebaseline: "proposing re-baseline",
  save_report: "saving report",
  open_alert: "opening alert",
  present_alert: "preparing decision",
  notify_owner: "messaging you",
  record_decision: "recording decision",
};

function humanTool(t: string) {
  return TOOL_LABEL[t] ?? t;
}
