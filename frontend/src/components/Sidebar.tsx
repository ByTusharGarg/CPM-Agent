"use client";

import Link from "next/link";
import { Bell, HardHat, LogOut, Plus } from "lucide-react";
import { api, ROLE_LABEL, type Me, type ProjectSummary } from "@/lib/api";
import { HealthDot } from "./ui";

export function Sidebar({ projects, activeId, me }: { projects: ProjectSummary[]; activeId: number; me?: Me | null }) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center gap-2 px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500 text-white">
          <HardHat size={18} />
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">Site Manager</div>
          <div className="text-xs text-slate-500">AI project manager</div>
        </div>
      </div>
      <div className="px-4 pb-2 pt-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">Projects</div>
      <nav className="scroll-thin flex-1 space-y-0.5 overflow-y-auto px-2">
        {projects.map((p) => {
          const active = p.id === activeId;
          return (
            <Link
              key={p.id}
              href={p.role === "contractor" ? `/projects/${p.id}/updates` : `/projects/${p.id}`}
              className={`flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-sm transition ${
                active ? "bg-slate-100" : "hover:bg-slate-50"
              }`}
            >
              <HealthDot status={p.health} className="mt-1.5" />
              <div className="min-w-0 flex-1">
                <div className={`truncate ${active ? "font-semibold" : "font-medium"}`}>{p.name}</div>
                <div className="truncate text-xs text-slate-500">
                  {p.role === "contractor" ? (
                    "Daily reports"
                  ) : (
                    <>
                      {p.actual_pct.toFixed(0)}% done ·{" "}
                      {p.slip_days > 0 ? <span className="text-red-600">{p.slip_days}d late</span> : "on schedule"}
                    </>
                  )}
                  <span className="text-slate-400"> · {ROLE_LABEL[p.role]}</span>
                </div>
              </div>
              {p.needs_you > 0 && (
                <span
                  title="Items that need your decision"
                  className="mt-0.5 rounded-full bg-red-500 px-1.5 text-[11px] font-semibold leading-5 text-white"
                >
                  {p.needs_you}
                </span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="space-y-2 border-t border-slate-200 p-3">
        {me && (
          <div className="flex items-center gap-2 px-1">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{me.name || me.email}</div>
              <div className="truncate text-xs text-slate-500">{me.email}</div>
            </div>
            <Link href="/settings" title="Notification settings" className="text-slate-500 hover:text-slate-900">
              <Bell size={16} />
            </Link>
            <button
              title="Sign out"
              onClick={async () => { await api.logout(); window.location.href = "/login"; }}
              className="text-slate-500 hover:text-slate-900"
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
        {me?.is_org_admin && (
        <Link
          href="/projects/new"
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium hover:bg-slate-50"
        >
          <Plus size={16} /> New project
        </Link>
        )}
      </div>
    </aside>
  );
}
