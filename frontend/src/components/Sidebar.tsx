"use client";

import Link from "next/link";
import { Bell, LogOut, Plus } from "lucide-react";
import { api, ROLE_LABEL, type Me, type ProjectSummary } from "@/lib/api";
import { HealthDot } from "./ui";

export function Sidebar({ projects, activeId, me }: { projects: ProjectSummary[]; activeId: number; me?: Me | null }) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-rule bg-panel">
      <div className="px-5 pb-4 pt-5">
        <div className="font-display text-[22px] font-bold leading-none text-ink">Site Manager</div>
        <div className="mt-1 text-[13px] text-graphite/70">Your projects, kept on programme</div>
      </div>
      <nav aria-label="Projects" className="scroll-thin flex-1 overflow-y-auto pb-2">
        {projects.map((p) => {
          const active = p.id === activeId;
          return (
            <Link
              key={p.id}
              href={p.role === "contractor" ? `/projects/${p.id}/updates` : `/projects/${p.id}`}
              aria-current={active ? "page" : undefined}
              className={`relative flex items-start gap-3 px-5 py-2.5 ${active ? "bg-concrete" : "hover:bg-concrete/60"}`}
            >
              {active && <span aria-hidden className="absolute inset-y-1.5 left-0 w-[3px] bg-ink" />}
              <HealthDot status={p.health} className="mt-[7px]" />
              <div className="min-w-0 flex-1">
                <div className={`font-display text-[17px] leading-snug ${active ? "font-semibold text-ink" : "font-medium text-graphite"}`}>
                  {p.name}
                </div>
                <div className="text-[13px] text-graphite/70">
                  {p.role === "contractor" ? (
                    "Daily reports"
                  ) : p.slip_days > 0 ? (
                    <span className="text-brick">{p.slip_days} days late</span>
                  ) : (
                    "On programme"
                  )}
                  <span className="ml-2 text-graphite/50">{ROLE_LABEL[p.role]}</span>
                </div>
              </div>
              {p.needs_you > 0 && (
                <span
                  title={`${p.needs_you} decision${p.needs_you === 1 ? "" : "s"} waiting for you`}
                  className="mt-0.5 rounded-sm bg-hivis px-1.5 font-display text-[14px] font-bold leading-5 text-ink"
                >
                  {p.needs_you}
                </span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="space-y-3 border-t border-rule px-5 py-4">
        {me && (
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-[14px] font-medium text-graphite">{me.name || me.email}</div>
              <div className="truncate text-[12px] text-graphite/60">{me.email}</div>
            </div>
            <Link href="/settings" title="Settings and notifications" className="rounded p-1 text-graphite/60 hover:text-ink">
              <Bell size={17} />
            </Link>
            <button
              title="Sign out"
              aria-label="Sign out"
              onClick={async () => {
                await api.logout();
                window.location.href = "/login";
              }}
              className="rounded p-1 text-graphite/60 hover:text-ink"
            >
              <LogOut size={17} />
            </button>
          </div>
        )}
        {me?.is_org_admin && (
          <Link
            href="/projects/new"
            className="flex w-full items-center justify-center gap-1.5 rounded border border-ink/30 px-3 py-2 text-[14px] font-medium text-ink hover:bg-concrete"
          >
            <Plus size={16} /> New project
          </Link>
        )}
      </div>
    </aside>
  );
}
