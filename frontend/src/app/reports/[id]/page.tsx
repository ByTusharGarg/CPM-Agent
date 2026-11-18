"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Download } from "lucide-react";
import { api, errMsg, fmtDate, type Report } from "@/lib/api";
import { Markdown } from "@/components/Markdown";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const [r, setR] = useState<(Report & { project_id?: number }) | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.report(Number(id)).then(setR).catch((e) => setErr(errMsg(e)));
  }, [id]);

  function download() {
    if (!r) return;
    const blob = new Blob([r.content], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `report-${r.id}.md`;
    a.click();
  }

  return (
    <main className="mx-auto max-w-3xl px-5 py-8">
      <div className="flex items-center justify-between">
        <Link href={r?.project_id ? `/projects/${r.project_id}` : "/"} className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
          <ArrowLeft size={14} /> Back
        </Link>
        {r && (
          <button onClick={download} className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm">
            <Download size={14} /> Markdown
          </button>
        )}
      </div>
      {err && <p className="mt-6 text-red-600">{err}</p>}
      {r && (
        <article className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
          <p className="text-xs text-slate-500">
            {r.period} · generated {fmtDate(r.created_at)}
          </p>
          <Markdown>{r.content}</Markdown>
        </article>
      )}
    </main>
  );
}
