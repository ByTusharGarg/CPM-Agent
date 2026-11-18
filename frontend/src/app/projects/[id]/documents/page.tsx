"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { SiteBoard } from "@/components/SiteBoard";
import { useHeader } from "@/lib/useHeader";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, FileText, Search, Upload } from "lucide-react";
import {
  api,
  errMsg,
  fileUrl,
  fmtDate,
  type DocItem,
  type Me,
  type Rfi,
  type SearchHit,
  type SubmittalItem,
} from "@/lib/api";
import { StatusPill } from "@/components/ui";

type Tab = "documents" | "rfis" | "submittals";

export default function DocumentsPage() {
  return (
    <Suspense>
      <DocumentsInner />
    </Suspense>
  );
}

function DocumentsInner() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const initial = (useSearchParams().get("tab") as Tab) || "documents";
  const [tab, setTab] = useState<Tab>(initial);
  const [me, setMe] = useState<Me | null>(null);
  const header = useHeader(pid);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
  }, []);
  const mine = me?.projects.find((p) => p.id === pid);
  const can = (perm: string) => !!mine?.permissions.includes(perm);
  const say = (ok: boolean, text: string) => setMsg({ ok, text });

  return (
    <>
    {mine?.role === "contractor" ? (
      <div className="bg-ink px-6 py-3">
        <Link href={`/projects/${pid}/updates`} className="flex items-center gap-1 text-[14px] text-white/80 hover:text-white">
          <ArrowLeft size={14} /> Back to daily report
        </Link>
      </div>
    ) : (
      <SiteBoard project={header.project} health={header.health} can={header.can} />
    )}
    <main className="mx-auto max-w-6xl px-5 py-6">
      <h2 className="text-[26px] font-bold leading-none">Drawings, RFIs and submittals</h2>
      <div className="mt-4 flex gap-1 border-b border-rule">
        {(["documents", "rfis", "submittals"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); setMsg(null); }}
            className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-ink font-semibold" : "text-graphite/70 hover:text-ink"}`}
          >
            {t === "documents" ? "Documents" : t === "rfis" ? "RFIs" : "Submittals"}
          </button>
        ))}
      </div>
      {msg && <p className={`mt-3 text-sm ${msg.ok ? "text-site" : "text-brick"}`}>{msg.text}</p>}
      <div className="mt-4">
        {tab === "documents" && <Documents pid={pid} can={can} say={say} />}
        {tab === "rfis" && <Rfis pid={pid} can={can} say={say} myUserId={me?.id} />}
        {tab === "submittals" && <Submittals pid={pid} can={can} say={say} />}
      </div>
    </main>
    </>
  );
}

type Ctx = { pid: number; can: (p: string) => boolean; say: (ok: boolean, t: string) => void };
const input = "w-full rounded border border-rule px-3 py-2 text-sm outline-none focus:border-ink/50";
const btn = "rounded bg-ink px-3.5 py-2 text-sm font-medium text-white disabled:opacity-40";
const DOC_STATUSES = ["draft", "for_review", "approved", "for_construction", "superseded", "rejected"];

// ---------------------------------------------------------------- documents

function Documents({ pid, can, say }: Ctx) {
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const load = () => api.documents(pid).then(setDocs).catch((e) => say(false, errMsg(e)));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const r = await api.uploadDocument(pid, f);
        say(true, r.new_document ? `Registered ${r.number || r.title} (${r.revision}).` : `${r.number} ${r.revision} uploaded - supersedes ${r.supersedes}.`);
      }
      load();
    } catch (e) {
      say(false, errMsg(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function runSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return setHits(null);
    try {
      setHits(await api.searchDocuments(pid, q));
    } catch (err) {
      say(false, errMsg(err));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <form onSubmit={runSearch} className="flex flex-1 items-center gap-2 rounded border border-rule bg-panel px-3">
          <Search size={15} className="text-graphite/50" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search drawings and specifications (current revisions)…" className="flex-1 py-2 text-sm outline-none" />
        </form>
        {can("document.upload") && (
          <>
            <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => upload(e.target.files)} />
            <button onClick={() => fileRef.current?.click()} disabled={busy} className={`${btn} flex items-center gap-1.5`}>
              <Upload size={14} /> {busy ? "Uploading…" : "Upload"}
            </button>
          </>
        )}
      </div>
      <p className="text-xs text-graphite/70">
        Name files like <code>A-101_R2 ground floor.pdf</code> and the number and revision are picked up automatically; a new revision supersedes the old one. The Document Controller agent checks and links each upload.
      </p>

      {hits && (
        <div className="rounded-md border border-rule bg-panel p-4">
          <div className="mb-2 text-sm font-medium">{hits.length} result(s) for “{q}”</div>
          <ul className="space-y-2">
            {hits.map((h) => (
              <li key={h.cite} className="text-sm">
                <span className="font-medium">{h.number || h.title}</span> <span className="text-graphite/70">{h.revision}, page {h.page}</span>
                <p className="mt-0.5 text-graphite/80">{h.excerpt}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-md border border-rule bg-panel">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-xs text-graphite/70">
              <th className="px-3 py-2">Number</th><th>Title</th><th>Discipline</th><th>Rev</th><th>Status</th><th>Updated</th><th />
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} className="border-b border-rule">
                <td className="px-3 py-2 font-display text-xs">{d.number || "—"}</td>
                <td className="pr-3">
                  {d.title}
                  {!d.classified && <span className="ml-1.5 rounded bg-ink/5 px-1 text-[10px] text-ink-soft">being checked</span>}
                  {!d.searchable && <span className="ml-1.5 rounded bg-concrete px-1 text-[10px] text-graphite/70">no text</span>}
                </td>
                <td className="pr-3 text-graphite/80">{d.discipline || "—"}</td>
                <td className="pr-3">{d.revision}</td>
                <td className="pr-3">
                  {can("document.manage") ? (
                    <select
                      value={d.status}
                      onChange={async (e) => {
                        try { await api.patchDocument(d.id, { status: e.target.value }); load(); } catch (err) { say(false, errMsg(err)); }
                      }}
                      className="rounded border border-rule px-1 py-0.5 text-xs"
                    >
                      {DOC_STATUSES.map((st) => <option key={st} value={st}>{st.replace("_", " ")}</option>)}
                    </select>
                  ) : (
                    <StatusPill status={d.status} />
                  )}
                </td>
                <td className="pr-3 text-xs text-graphite/70">{fmtDate(d.updated_at)}</td>
                <td className="pr-3">
                  {d.version_id && (
                    <a href={fileUrl(d.version_id)} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-graphite/80 hover:text-ink">
                      <FileText size={13} /> Open
                    </a>
                  )}
                </td>
              </tr>
            ))}
            {docs.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-center text-graphite/70">No documents yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- RFIs

function Rfis({ pid, can, say, myUserId }: Ctx & { myUserId?: number }) {
  const [rfis, setRfis] = useState<Rfi[]>([]);
  const [form, setForm] = useState({ subject: "", question: "" });
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const load = () => api.rfis(pid).then(setRfis).catch((e) => say(false, errMsg(e)));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function raise() {
    try {
      const r = await api.raiseRfi(pid, form);
      say(true, `RFI #${r.number} raised - answer due ${fmtDate(r.due_date)}.`);
      setForm({ subject: "", question: "" });
      load();
    } catch (e) {
      say(false, errMsg(e));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
      <ul className="space-y-2">
        {rfis.map((r) => (
          <li key={r.id} className="rounded-md border border-rule bg-panel p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display text-xs text-graphite/70">RFI #{r.number}</span>
              <span className="font-medium">{r.subject}</span>
              <StatusPill status={r.status} />
              {r.overdue && <span className="rounded bg-brick/10 px-1.5 text-[11px] font-semibold text-brick">Overdue</span>}
              {r.time_impact && <span className="rounded bg-hivis-soft px-1.5 text-[11px] text-[#7a5a00]">schedule impact</span>}
              <span className="ml-auto text-xs text-graphite/70">Due {fmtDate(r.due_date)}, with {r.ball_in_court}</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm text-graphite">{r.question}</p>
            {r.answer && <p className="mt-2 rounded bg-site/10 px-3 py-2 text-sm text-site"><b>Answer:</b> {r.answer}</p>}
            {r.status === "open" && can("rfi.answer") && (
              <div className="mt-2 flex gap-2">
                <input className={input} placeholder="Answer…" value={answers[r.id] ?? ""} onChange={(e) => setAnswers({ ...answers, [r.id]: e.target.value })} />
                <button
                  className={btn}
                  disabled={!answers[r.id]?.trim()}
                  onClick={async () => {
                    try { await api.answerRfi(r.id, answers[r.id]); say(true, `RFI #${r.number} answered.`); load(); } catch (e) { say(false, errMsg(e)); }
                  }}
                >
                  Answer
                </button>
              </div>
            )}
            {r.status === "answered" && (r.raised_by === myUserId || can("rfi.answer")) && (
              <button
                className="mt-2 text-xs font-medium text-graphite/80 underline"
                onClick={async () => { try { await api.closeRfi(r.id); load(); } catch (e) { say(false, errMsg(e)); } }}
              >
                Close RFI
              </button>
            )}
          </li>
        ))}
        {rfis.length === 0 && <li className="text-sm text-graphite/70">No RFIs.</li>}
      </ul>
      {can("rfi.create") && (
        <div className="h-fit space-y-2 rounded-md border border-rule bg-panel p-4">
          <div className="text-sm font-medium">Raise an RFI</div>
          <input className={input} placeholder="Subject" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          <textarea className={input} rows={5} placeholder="Question - reference drawing numbers and locations" value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} />
          <button className={btn} disabled={form.subject.trim().length < 3 || form.question.trim().length < 3} onClick={raise}>Raise RFI</button>
          <p className="text-xs text-graphite/70">Contractors can also send <code>RFI: …</code> on WhatsApp.</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- submittals

const REVIEW = [
  ["approved", "Approve"],
  ["approved_as_noted", "Approve as noted"],
  ["revise_resubmit", "Revise & resubmit"],
  ["rejected", "Reject"],
] as const;

function Submittals({ pid, can, say }: Ctx) {
  const [items, setItems] = useState<SubmittalItem[]>([]);
  const [form, setForm] = useState({ title: "", spec_section: "", file: null as File | null });
  const [comments, setComments] = useState<Record<number, string>>({});
  const load = () => api.submittals(pid).then(setItems).catch((e) => say(false, errMsg(e)));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    try {
      const r = await api.submitSubmittal(pid, form);
      say(true, `Submittal #${r.number} submitted - review due ${fmtDate(r.due_date)}.`);
      setForm({ title: "", spec_section: "", file: null });
      load();
    } catch (e) {
      say(false, errMsg(e));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
      <ul className="space-y-2">
        {items.map((x) => (
          <li key={x.id} className="rounded-md border border-rule bg-panel p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-display text-xs text-graphite/70">Submittal {x.number}, revision {x.revision}</span>
              <span className="font-medium">{x.title}</span>
              {x.spec_section && <span className="text-xs text-graphite/70">spec {x.spec_section}</span>}
              <StatusPill status={x.status} />
              {x.overdue && <span className="rounded bg-brick/10 px-1.5 text-[11px] font-semibold text-brick">Review overdue</span>}
              <span className="ml-auto text-xs text-graphite/70">
                Review due {fmtDate(x.due_date)}{x.required_by ? `, needed on site by ${fmtDate(x.required_by)}` : ""}
              </span>
            </div>
            {x.reviewer_comments && <p className="mt-2 text-sm text-graphite"><b>Reviewer:</b> {x.reviewer_comments}</p>}
            {["submitted", "under_review"].includes(x.status) && can("submittal.review") && (
              <div className="mt-2 space-y-2">
                <input className={input} placeholder="Review comments" value={comments[x.id] ?? ""} onChange={(e) => setComments({ ...comments, [x.id]: e.target.value })} />
                <div className="flex flex-wrap gap-2">
                  {REVIEW.map(([st, label]) => (
                    <button
                      key={st}
                      className="rounded border border-rule px-2.5 py-1 text-xs font-medium hover:bg-concrete"
                      onClick={async () => {
                        try { await api.reviewSubmittal(x.id, st, comments[x.id] ?? ""); say(true, `Submittal #${x.number}: ${label}.`); load(); } catch (e) { say(false, errMsg(e)); }
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {["revise_resubmit", "rejected"].includes(x.status) && can("submittal.submit") && (
              <label className="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded border border-rule px-2.5 py-1 text-xs font-medium hover:bg-concrete">
                <Upload size={12} /> Resubmit revision {x.revision + 1}
                <input
                  type="file"
                  className="hidden"
                  onChange={async (e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    try { await api.resubmitSubmittal(x.id, f, ""); say(true, `Submittal #${x.number} resubmitted.`); load(); } catch (err) { say(false, errMsg(err)); }
                  }}
                />
              </label>
            )}
          </li>
        ))}
        {items.length === 0 && <li className="text-sm text-graphite/70">No submittals.</li>}
      </ul>
      {can("submittal.submit") && (
        <div className="h-fit space-y-2 rounded-md border border-rule bg-panel p-4">
          <div className="text-sm font-medium">New submittal</div>
          <input className={input} placeholder="Title, e.g. Metal deck profile" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <input className={input} placeholder="Spec section, e.g. 05 31 00" value={form.spec_section} onChange={(e) => setForm({ ...form, spec_section: e.target.value })} />
          <input type="file" className="text-sm" onChange={(e) => setForm({ ...form, file: e.target.files?.[0] ?? null })} />
          <button className={btn} disabled={!form.title.trim()} onClick={submit}>Submit for review</button>
        </div>
      )}
    </div>
  );
}
