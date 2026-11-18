"use client";

import { useMemo, useRef, useState } from "react";
import { money, type CashflowPoint } from "@/lib/api";

/*
  Cumulative spend S-curve. Palette = validated categorical slots 1-3 on the panel
  surface (#F7F8F6): all six checks pass; aqua is below 3:1 contrast, so every
  series carries a direct end-label plus a legend, and a table view is available.
*/
const SERIES = [
  { key: "planned_cum", label: "Planned", color: "#2a78d6" },
  { key: "actual_cum", label: "Actual", color: "#eb6834" },
  { key: "forecast_cum", label: "Forecast", color: "#1baf7a" },
] as const;

const W = 760;
const H = 300;
const PAD = { l: 64, r: 92, t: 16, b: 34 };

function monthLabel(m: string) {
  const [y, mm] = m.split("-").map(Number);
  return new Date(y, mm - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

export function CashflowChart({ data, budget, currency }: { data: CashflowPoint[]; budget?: number; currency: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  const { x, y, ticks, max } = useMemo(() => {
    const vals = data.flatMap((d) => [d.planned_cum, d.forecast_cum, d.actual_cum ?? 0]);
    const max = Math.max(...vals, budget ?? 0) * 1.05 || 1;
    const x = (i: number) => PAD.l + (i / Math.max(data.length - 1, 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b);
    const step = max / 4;
    return { x, y, max, ticks: [0, 1, 2, 3, 4].map((k) => k * step) };
  }, [data, budget]);

  if (!data.length) return <p className="text-[14px] text-graphite/70">No cash-flow data yet.</p>;

  const path = (key: (typeof SERIES)[number]["key"]) => {
    let d = "";
    data.forEach((p, i) => {
      const v = p[key];
      if (v == null) return;
      d += `${d ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
    });
    return d;
  };
  const lastIdx = (key: (typeof SERIES)[number]["key"]) => {
    for (let i = data.length - 1; i >= 0; i--) if (data[i][key] != null) return i;
    return -1;
  };

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (data.length - 1));
    setHover(i >= 0 && i < data.length ? i : null);
  }

  const labelStep = Math.max(1, Math.ceil(data.length / 8));
  // End labels: keep them apart vertically, but only by nudging within 14px (no detached labels)
  const ends = SERIES.map((s) => ({ ...s, i: lastIdx(s.key) })).filter((s) => s.i >= 0)
    .map((s) => ({ ...s, yy: y(data[s.i][s.key] as number) })).sort((a, b) => a.yy - b.yy);
  for (let k = 1; k < ends.length; k++) if (ends[k].yy - ends[k - 1].yy < 14) ends[k].yy = ends[k - 1].yy + 14;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-4 text-[13px] text-graphite">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <svg width="18" height="8" aria-hidden><line x1="0" y1="4" x2="18" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.key === "forecast_cum" ? "5 3" : undefined} /></svg>
            {s.label}
          </span>
        ))}
        {budget ? (
          <span className="flex items-center gap-1.5">
            <svg width="18" height="8" aria-hidden><line x1="0" y1="4" x2="18" y2="4" stroke="#16324f" strokeWidth="1" /></svg>
            Budget
          </span>
        ) : null}
        <button onClick={() => setAsTable(!asTable)} className="ml-auto rounded border border-rule px-2 py-0.5 text-[13px] text-ink hover:bg-concrete">
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>

      {asTable ? (
        <div className="max-h-[300px] overflow-auto rounded border border-rule">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-panel text-left text-graphite/70">
              <tr><th className="px-3 py-1.5 font-medium">Month</th>{SERIES.map((s) => <th key={s.key} className="px-3 py-1.5 text-right font-medium">{s.label} to date</th>)}</tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.month} className="border-t border-rule/70">
                  <td className="px-3 py-1">{monthLabel(d.month)}</td>
                  {SERIES.map((s) => <td key={s.key} className="px-3 py-1 text-right">{d[s.key] == null ? "—" : money(d[s.key] as number, currency)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full"
            role="img"
            aria-label="Cumulative spend: planned, actual and forecast by month"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
          >
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="#dfe2dc" strokeWidth="1" />
                <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#5b636b">{money(t, currency)}</text>
              </g>
            ))}
            {data.map((d, i) => i % labelStep === 0 && (
              <text key={d.month} x={x(i)} y={H - 10} textAnchor="middle" fontSize="11" fill="#5b636b">{monthLabel(d.month)}</text>
            ))}
            {budget ? (
              <g>
                <line x1={PAD.l} x2={W - PAD.r} y1={y(budget)} y2={y(budget)} stroke="#16324f" strokeWidth="1" />
                <text x={PAD.l + 4} y={y(budget) - 5} fontSize="11" fill="#16324f">Budget {money(budget, currency)}</text>
              </g>
            ) : null}
            {SERIES.map((s) => (
              <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
                    strokeDasharray={s.key === "forecast_cum" ? "6 4" : undefined} />
            ))}
            {ends.map((s) => (
              <g key={s.key}>
                <circle cx={x(s.i)} cy={y(data[s.i][s.key] as number)} r="4" fill={s.color} stroke="#f7f8f6" strokeWidth="2" />
                <text x={x(s.i) + 8} y={s.yy + 4} fontSize="12" fill="#2a3036">{s.label} {money(data[s.i][s.key] as number, currency)}</text>
              </g>
            ))}
            {hover != null && (
              <g pointerEvents="none">
                <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="#5b636b" strokeWidth="1" />
                {SERIES.map((s) => data[hover][s.key] != null && (
                  <circle key={s.key} cx={x(hover)} cy={y(data[hover][s.key] as number)} r="4.5" fill={s.color} stroke="#f7f8f6" strokeWidth="2" />
                ))}
              </g>
            )}
            <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} fill="transparent" />
          </svg>
          {hover != null && (
            <div
              className="pointer-events-none absolute top-2 rounded border border-rule bg-panel px-3 py-2 text-[13px] shadow-sm"
              style={{ left: `${Math.min((x(hover) / W) * 100, 70)}%` }}
            >
              <div className="font-display font-semibold text-ink">{monthLabel(data[hover].month)}</div>
              {SERIES.map((s) => (
                <div key={s.key} className="flex items-center gap-2 text-graphite">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
                  {s.label}: {data[hover][s.key] == null ? "—" : money(data[hover][s.key] as number, currency)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <p className="sr-only">Maximum shown {money(max, currency)}.</p>
    </div>
  );
}
