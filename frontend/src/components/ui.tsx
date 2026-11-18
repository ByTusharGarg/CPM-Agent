import type { ReactNode } from "react";
import type { Health } from "@/lib/api";

const dot: Record<string, string> = {
  green: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
};

export function HealthDot({ status, className = "" }: { status: Health | string; className?: string }) {
  return <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${dot[status] ?? "bg-slate-300"} ${className}`} />;
}

const sev: Record<string, string> = {
  critical: "bg-red-50 text-red-700 ring-red-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  info: "bg-sky-50 text-sky-700 ring-sky-200",
};

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ${sev[severity] ?? sev.info}`}>
      {severity}
    </span>
  );
}

export function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-b border-slate-200 px-4 py-4">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

const statusColor: Record<string, string> = {
  running: "bg-sky-100 text-sky-700",
  queued: "bg-slate-100 text-slate-600",
  done: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-500",
  executed: "bg-emerald-100 text-emerald-700",
  approved: "bg-emerald-100 text-emerald-700",
  observed: "bg-violet-100 text-violet-700",
  pending_approval: "bg-amber-100 text-amber-800",
  rejected: "bg-red-100 text-red-700",
  blocked: "bg-slate-100 text-slate-500",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${statusColor[status] ?? "bg-slate-100 text-slate-600"}`}>
      {status.replace("_", " ")}
    </span>
  );
}

export const WORKER_LABEL: Record<string, string> = {
  design: "Design",
  procurement: "Procurement",
  site_updates: "Site updates",
  cost: "Cost",
  planner: "Planner",
  reporter: "Reporter",
};
