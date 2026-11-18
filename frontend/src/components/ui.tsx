import type { ReactNode } from "react";
import type { Health } from "@/lib/api";

const dot: Record<string, string> = {
  green: "bg-site",
  amber: "bg-hivis",
  red: "bg-brick",
};
const healthLabel: Record<string, string> = { green: "On track", amber: "Watch", red: "Late" };

/** Status never relies on colour alone: the dot carries a title and screen-reader label. */
export function HealthDot({ status, className = "" }: { status: Health | string; className?: string }) {
  return (
    <span
      role="img"
      aria-label={healthLabel[status] ?? "Unknown"}
      title={healthLabel[status] ?? "Unknown"}
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${dot[status] ?? "bg-rule"} ${className}`}
    />
  );
}

const sev: Record<string, string> = {
  critical: "border-brick text-brick",
  warning: "border-[#b8890b] text-[#7a5a00]",
  info: "border-ink-soft text-ink-soft",
};
const sevLabel: Record<string, string> = { critical: "Critical", warning: "Warning", info: "Info" };

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={`rounded-sm border px-1.5 text-[12px] font-semibold leading-5 ${sev[severity] ?? sev.info}`}>
      {sevLabel[severity] ?? severity}
    </span>
  );
}

export function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-b border-rule px-4 py-4">
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h3 className="font-display text-[17px] font-semibold leading-tight text-ink">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

const statusColor: Record<string, string> = {
  running: "text-ink-soft",
  queued: "text-graphite/70",
  done: "text-site",
  failed: "text-brick",
  cancelled: "text-graphite/60",
  executed: "text-site",
  approved: "text-site",
  approved_as_noted: "text-site",
  certified: "text-site",
  answered: "text-site",
  for_construction: "text-site",
  observed: "text-[#6250d6]",
  pending_approval: "text-[#7a5a00]",
  awaiting_owner: "text-[#7a5a00]",
  submitted: "text-[#7a5a00]",
  under_review: "text-[#7a5a00]",
  for_review: "text-[#7a5a00]",
  open: "text-[#7a5a00]",
  revise_resubmit: "text-brick",
  rejected: "text-brick",
  blocked: "text-graphite/60",
};

/** Status in words (sentence case) with a small leading mark - readable without colour. */
export function StatusPill({ status }: { status: string }) {
  const text = status.replace(/_/g, " ");
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap text-[13px] font-medium ${statusColor[status] ?? "text-graphite/70"}`}>
      <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {text.charAt(0).toUpperCase() + text.slice(1)}
    </span>
  );
}

export const WORKER_LABEL: Record<string, string> = {
  design: "Design coordinator",
  procurement: "Procurement",
  site_updates: "Site updates",
  cost: "Commercial manager",
  planner: "Planner",
  reporter: "Report writer",
  document_controller: "Document controller",
};
