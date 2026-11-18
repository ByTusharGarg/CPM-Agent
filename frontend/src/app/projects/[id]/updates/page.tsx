"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ImagePlus, LogOut, Send, X } from "lucide-react";
import { api, errMsg, ROLE_LABEL, type Me } from "@/lib/api";

type C = { id: number; name: string; trade: string; allowlisted: boolean };
type Photo = { id: number; filename: string; preview: string };

export default function DailyReportPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const [me, setMe] = useState<Me | null>(null);
  const [contractors, setContractors] = useState<C[]>([]);
  const [cid, setCid] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    api
      .contractors(pid)
      .then((cs) => {
        setContractors(cs);
        if (cs.length) setCid(cs[0].id);
      })
      .catch((e) => setStatus({ ok: false, msg: errMsg(e) }));
  }, [pid]);

  const role = me?.projects.find((p) => p.id === pid)?.role;
  const isContractor = role === "contractor";

  async function addPhotos(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        const r = await api.upload(pid, f);
        setPhotos((p) => [...p, { id: r.id, filename: r.filename, preview: URL.createObjectURL(f) }]);
      }
    } catch (e) {
      setStatus({ ok: false, msg: errMsg(e) });
    } finally {
      setUploading(false);
    }
  }

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.dailyUpdate(pid, {
        contractor_id: isContractor ? undefined : cid ?? undefined,
        text,
        report_date: date,
        file_ids: photos.map((p) => p.id),
      });
      setStatus(
        r.event_id
          ? { ok: true, msg: `Received (report #${r.update_id}). The site-updates agent is processing it.` }
          : { ok: false, msg: "Not accepted: the company is not allowlisted, or this duplicates a recent report." },
      );
      if (r.event_id) {
        setText("");
        setPhotos([]);
      }
    } catch (e) {
      setStatus({ ok: false, msg: errMsg(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-xl px-5 py-8">
      <div className="flex items-center justify-between">
        {isContractor ? (
          <span className="text-sm text-graphite/70">
            {me?.name || me?.email}, {ROLE_LABEL.contractor.toLowerCase()}
          </span>
        ) : (
          <Link href={`/projects/${pid}`} className="flex items-center gap-1 text-sm text-graphite/70 hover:text-ink">
            <ArrowLeft size={14} /> Back to project
          </Link>
        )}
        <button
          onClick={async () => {
            await api.logout();
            window.location.href = "/login";
          }}
          className="flex items-center gap-1 text-sm text-graphite/70 hover:text-ink"
        >
          <LogOut size={14} /> Sign out
        </button>
      </div>
      <h1 className="mt-4 text-xl font-semibold">Daily site report</h1>
      <Link href={`/projects/${pid}/documents?tab=rfis`} className="mt-1 inline-block text-sm text-graphite/80 underline">
        Drawings, RFIs & submittals
      </Link>
      <p className="text-sm text-graphite/70">
        {isContractor ? `Reporting for ${contractors[0]?.name ?? "your company"}` : "Submit a report on a contractor's behalf"}
      </p>

      <div className="mt-5 space-y-4 rounded-md border border-rule bg-panel p-5">
        {!isContractor && (
          <label className="block">
            <span className="text-sm font-medium">Contractor</span>
            <select value={cid ?? ""} onChange={(e) => setCid(Number(e.target.value))} className="mt-1 w-full rounded border border-rule px-3 py-2 text-sm">
              {contractors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} — {c.trade}
                  {!c.allowlisted ? " (not allowlisted)" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="block">
          <span className="text-sm font-medium">Report date</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="mt-1 w-full rounded border border-rule px-3 py-2 text-sm" />
        </label>
        <label className="block">
          <span className="text-sm font-medium">What happened today?</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            placeholder="Work done, % complete, manpower, materials received, issues or blockers…"
            className="mt-1 w-full rounded border border-rule px-3 py-2 text-sm"
          />
        </label>
        <div>
          <span className="text-sm font-medium">Photos</span>
          <div className="mt-1 flex flex-wrap gap-2">
            {photos.map((p) => (
              <div key={p.id} className="relative h-20 w-20 overflow-hidden rounded border border-rule">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.preview} alt={p.filename} className="h-full w-full object-cover" />
                <button
                  aria-label="Remove photo"
                  onClick={() => setPhotos((ps) => ps.filter((x) => x.id !== p.id))}
                  className="absolute right-0.5 top-0.5 rounded-full bg-panel/90 p-0.5"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            <label className="flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded border border-dashed border-rule text-xs text-graphite/70 hover:bg-concrete">
              <ImagePlus size={18} />
              {uploading ? "Uploading…" : "Add"}
              <input type="file" accept="image/*" capture="environment" multiple className="hidden" onChange={(e) => addPhotos(e.target.files)} />
            </label>
          </div>
        </div>
        <button
          onClick={submit}
          disabled={busy || uploading || !text.trim()}
          className="flex items-center gap-1.5 rounded bg-ink px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          <Send size={14} /> Submit report
        </button>
        {status && <p className={`text-sm ${status.ok ? "text-site" : "text-brick"}`}>{status.msg}</p>}
      </div>
    </main>
  );
}
