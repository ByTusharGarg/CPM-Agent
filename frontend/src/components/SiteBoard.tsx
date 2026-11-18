"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Wifi, WifiOff } from "lucide-react";
import { fmtDate, type Project, type ProjectHealth } from "@/lib/api";

/**
 * The project header, modelled on the statutory board at a site gate: project and
 * client on the left, the completion date - planned and as forecast now - set large
 * on the right. It is the one loud element on the page; everything else stays quiet.
 */
export function SiteBoard({
  project,
  health,
  connected,
  can,
}: {
  project: Project | null;
  health: ProjectHealth | null;
  connected?: boolean;
  can: (perm: string) => boolean;
}) {
  const pathname = usePathname();
  const late = health ? health.project_slip_days : 0;
  const base = `/projects/${project?.id}`;
  const tabs = [
    { href: base, label: "Manager" },
    { href: `${base}/schedule`, label: "Schedule" },
    { href: `${base}/documents`, label: "Documents" },
    { href: `${base}/commercial`, label: "Money", show: can("commercial.view") },
    { href: `${base}/updates`, label: "Daily report", show: can("update.submit") },
  ].filter((t) => t.show !== false);

  return (
    <header className="bg-ink text-white">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3 px-6 pb-3 pt-4">
        <div className="min-w-0 flex-1">
          <h1 className="line-clamp-2 font-display text-[28px] font-bold leading-[1.05] tracking-[0.01em] text-white">
            {project?.name ?? "…"}
          </h1>
          <p className="mt-1.5 truncate text-[14px] text-white/70">
            {project ? <>{project.location}<span className="mx-2 text-white/30">/</span>{project.owner_name}</> : " "}
          </p>
        </div>
        {health && (
          <dl className="flex items-end gap-7 font-display">
            <div>
              <dt className="text-[13px] font-medium text-white/60">Planned completion</dt>
              <dd className="text-[22px] font-semibold leading-tight">{fmtDate(health.target_end)}</dd>
            </div>
            <div>
              <dt className="text-[13px] font-medium text-white/60">Forecast completion</dt>
              <dd className={`text-[22px] font-semibold leading-tight ${late > 0 ? "text-hivis" : "text-white"}`}>
                {fmtDate(health.forecast_end)}
              </dd>
            </div>
            <div className="min-w-[88px]">
              <dt className="text-[13px] font-medium text-white/60">Programme</dt>
              <dd className="text-[22px] font-semibold leading-tight">
                {late > 0 ? <span className="text-hivis">{late} days late</span> : <span>On time</span>}
              </dd>
            </div>
          </dl>
        )}
      </div>
      <nav aria-label="Project sections" className="flex items-center gap-1 px-4">
        {tabs.map((t) => {
          const active = pathname === t.href;
          return (
            <Link
              key={t.href}
              href={t.href}
              aria-current={active ? "page" : undefined}
              className={`border-b-[3px] px-2.5 pb-2 pt-1 font-display text-[16px] font-medium ${
                active ? "border-hivis text-white" : "border-transparent text-white/65 hover:text-white"
              }`}
            >
              {t.label}
            </Link>
          );
        })}
        {connected !== undefined && (
          <span title={connected ? "Live updates on" : "Reconnecting…"} className={`ml-auto pb-2 ${connected ? "text-white/60" : "text-hivis"}`}>
            {connected ? <Wifi size={15} /> : <WifiOff size={15} />}
          </span>
        )}
      </nav>
    </header>
  );
}
