"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, errMsg, fmtDate, money, type Commercial, type OverbillItem, type PayAppItem } from "@/lib/api";
import { SiteBoard } from "@/components/SiteBoard";
import { CashflowChart } from "@/components/CashflowChart";
import { StatusPill } from "@/components/ui";
import { useHeader } from "@/lib/useHeader";

export default function MoneyPage() {
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const header = useHeader(pid);
  const [data, setData] = useState<Commercial | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = () => api.commercial(pid).then(setData).catch((e) => setMsg({ ok: false, text: errMsg(e) }));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const cur = data?.currency ?? "INR";
  const s = data?.summary;
  const say = (ok: boolean, text: string) => { setMsg({ ok, text }); load(); };
  const contractName = (cid: number | null) => data?.contracts.find((c) => c.id === cid)?.title ?? "No contract";

  return (
    <>
      <SiteBoard project={header.project} health={header.health} can={header.can} />
      <main className="mx-auto max-w-6xl space-y-8 px-5 py-6">
        {msg && <p className={`text-[14px] ${msg.ok ? "text-site" : "text-brick"}`}>{msg.text}</p>}

        {s?.forecast_final != null && (
          <section aria-labelledby="final-cost">
            <h2 id="final-cost" className="text-[26px] font-bold leading-none">Where the money ends up</h2>
            <p className="mt-1 text-[14px] text-graphite/70">Forecast final cost is what is committed, plus changes still being decided, plus work not yet let.</p>
            <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-3 font-display">
              <Term label="Committed" value={money(s.committed!, cur)} note={`incl. ${money(s.approved_changes!, cur)} approved changes`} />
              <Op>+</Op>
              <Term label="Changes being decided" value={money(s.pending_exposure!, cur)} />
              <Op>+</Op>
              <Term label="Not yet let" value={money(s.uncommitted!, cur)} />
              <Op>=</Op>
              <Term label="Forecast final cost" value={money(s.forecast_final, cur)} strong />
              <div className="ml-2 border-l border-rule pl-4">
                <div className="text-[13px] font-medium text-graphite/70">Against budget {money(s.budget!, cur)}</div>
                <div className={`text-[24px] font-semibold leading-tight ${s.variance! > 0 ? "text-brick" : "text-site"}`}>
                  {s.variance! > 0 ? `${money(s.variance!, cur)} over` : `${money(-s.variance!, cur)} under`}
                </div>
              </div>
            </div>
          </section>
        )}

        {s?.cashflow && (
          <section aria-labelledby="cashflow" className="rounded-md border border-rule bg-panel p-5">
            <h2 id="cashflow" className="text-[20px] font-semibold leading-tight">Spend over time</h2>
            <p className="mb-3 text-[14px] text-graphite/70">Cumulative cost: the plan from the baseline, actual certified cost, and the forecast from today&apos;s schedule.</p>
            <CashflowChart data={s.cashflow} budget={s.budget} currency={cur} />
          </section>
        )}

        <PayApps data={data} cur={cur} can={header.can} say={say} contractName={contractName} />
        <ChangeOrders data={data} cur={cur} can={header.can} say={say} contractName={contractName} />

        {s?.contracts && s.contracts.length > 0 && (
          <section aria-labelledby="contracts">
            <h2 id="contracts" className="text-[20px] font-semibold leading-tight">Contracts</h2>
            <div className="mt-3 overflow-x-auto rounded-md border border-rule bg-panel">
              <table className="w-full min-w-[720px] text-[14px]">
                <thead>
                  <tr className="border-b border-rule text-left text-[13px] text-graphite/70">
                    <th className="px-4 py-2 font-medium">Contract</th>
                    <th className="px-3 text-right font-medium">Value incl. changes</th>
                    <th className="px-3 text-right font-medium">Work done on site</th>
                    <th className="px-3 text-right font-medium">Claimed</th>
                    <th className="px-3 text-right font-medium">Certified</th>
                    <th className="px-4 text-right font-medium">Claimed ahead of site</th>
                  </tr>
                </thead>
                <tbody>
                  {s.contracts.map((c) => (
                    <tr key={c.id} className="border-b border-rule/60">
                      <td className="px-4 py-2.5 font-medium text-graphite">{c.title}</td>
                      <td className="px-3 text-right">{money(c.revised, cur)}</td>
                      <td className="px-3 text-right">{money(c.earned, cur)}</td>
                      <td className="px-3 text-right">{money(c.claimed_to_date, cur)}</td>
                      <td className="px-3 text-right">{money(c.certified_to_date, cur)}</td>
                      <td className={`px-4 text-right ${c.overbilled > 0 ? "font-semibold text-brick" : "text-graphite/60"}`}>
                        {c.overbilled > 0 ? money(c.overbilled, cur) : "None"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {data && data.contracts.length === 0 && (
          <p className="text-[14px] text-graphite/70">No contracts are set up for this project yet, so there is no commercial position to show.</p>
        )}
      </main>
    </>
  );
}

function Term({ label, value, note, strong }: { label: string; value: string; note?: string; strong?: boolean }) {
  return (
    <div>
      <div className="text-[13px] font-medium text-graphite/70">{label}</div>
      <div className={`leading-tight ${strong ? "text-[30px] font-bold text-ink" : "text-[24px] font-semibold text-graphite"}`}>{value}</div>
      {note && <div className="text-[12px] text-graphite/60">{note}</div>}
    </div>
  );
}

function Op({ children }: { children: React.ReactNode }) {
  return <div aria-hidden className="pb-1 text-[24px] text-graphite/40">{children}</div>;
}

type Props = {
  data: Commercial | null;
  cur: string;
  can: (p: string) => boolean;
  say: (ok: boolean, t: string) => void;
  contractName: (id: number | null) => string;
};

const input = "w-full rounded border border-rule bg-panel px-3 py-2 text-[14px] outline-none focus:border-ink";
const primary = "rounded bg-ink px-3.5 py-2 text-[14px] font-medium text-white disabled:opacity-40";
const quiet = "rounded border border-rule px-3 py-1.5 text-[14px] text-ink hover:bg-concrete disabled:opacity-40";

function PayApps({ data, cur, can, say, contractName }: Props) {
  const apps = data?.pay_apps ?? [];
  const over = data?.summary?.overbilling ?? [];
  return (
    <section aria-labelledby="payapps">
      <h2 id="payapps" className="text-[20px] font-semibold leading-tight">Pay applications</h2>
      {apps.length === 0 ? (
        <p className="mt-2 text-[14px] text-graphite/70">No pay applications yet.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {apps.map((a) => (
            <PayAppRow key={a.id} app={a} cur={cur} can={can} say={say} contractName={contractName} data={data}
                       overbilled={over.filter((o) => o.pay_app_id === a.id)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PayAppRow({ app, cur, can, say, contractName, data, overbilled }: Omit<Props, "data"> & { app: PayAppItem; data: Commercial | null; overbilled: OverbillItem[] }) {
  const contract = data?.contracts.find((c) => c.id === app.contract_id);
  const recorded = Object.fromEntries(overbilled.map((o) => [o.line_id, o.recorded_pct]));
  const [pcts, setPcts] = useState<Record<number, number>>(() =>
    Object.fromEntries(app.lines.map((l) => [l.contract_line_id, recorded[l.contract_line_id] ?? l.claimed_pct])));
  const [note, setNote] = useState(overbilled.length ? "Certified at progress recorded on site." : "");
  const [busy, setBusy] = useState(false);
  const open = app.status === "submitted";
  const attention = open && can("payapp.certify");

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    try { await fn(); say(true, ok); } catch (e) { say(false, errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <li className={`rounded-md border border-rule bg-panel p-4 ${attention ? "border-l-[5px] border-l-hivis" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-display text-[18px] font-semibold text-ink">Pay application {app.number}</span>
        <span className="text-[14px] text-graphite/80">{contractName(app.contract_id)}, period to {fmtDate(app.period_end)}</span>
        <StatusPill status={app.status} />
        <span className="ml-auto font-display text-[18px] font-semibold text-graphite">
          {app.status === "certified" ? `Payable ${money(app.amount_due, cur)}` : `Claimed ${money(app.gross_to_date, cur)} to date`}
        </span>
      </div>
      {overbilled.length > 0 && (
        <p className="mt-2 text-[14px] text-brick">
          Claims run ahead of the work recorded on site on {overbilled.length} line{overbilled.length > 1 ? "s" : ""}, about{" "}
          {money(overbilled.reduce((t, o) => t + o.excess_value, 0), cur)} in total.
        </p>
      )}
      <table className="mt-3 w-full text-[14px]">
        <thead>
          <tr className="text-left text-[13px] text-graphite/70">
            <th className="py-1 font-medium">Line</th>
            <th className="text-right font-medium">Claimed</th>
            <th className="text-right font-medium">On site</th>
            <th className="text-right font-medium">{open ? "Certify at" : "Certified"}</th>
          </tr>
        </thead>
        <tbody>
          {app.lines.map((l) => {
            const line = contract?.lines.find((x) => x.id === l.contract_line_id);
            const rec = recorded[l.contract_line_id];
            return (
              <tr key={l.contract_line_id} className="border-t border-rule/60">
                <td className="py-1.5">{line?.description ?? `Line ${l.contract_line_id}`}</td>
                <td className="text-right">{l.claimed_pct}%</td>
                <td className={`text-right ${rec != null ? "font-semibold text-brick" : "text-graphite/60"}`}>{rec != null ? `${rec}%` : "In line"}</td>
                <td className="text-right">
                  {attention ? (
                    <input
                      type="number" min={0} max={l.claimed_pct} step={1}
                      aria-label={`Certify ${line?.description ?? "line"} at percent`}
                      value={pcts[l.contract_line_id] ?? l.claimed_pct}
                      onChange={(e) => setPcts({ ...pcts, [l.contract_line_id]: Number(e.target.value) })}
                      className="w-20 rounded border border-rule bg-panel px-2 py-1 text-right"
                    />
                  ) : (
                    l.certified_pct != null ? `${l.certified_pct}%` : "Not yet"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {attention && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className={`${input} max-w-md`} placeholder="Note to the contractor" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className={primary} disabled={busy} onClick={() => act(() => api.certifyPayApp(app.id, pcts, note), `Pay application ${app.number} certified.`)}>
            Certify
          </button>
          <button className={quiet} disabled={busy || !note.trim()} onClick={() => act(() => api.rejectPayApp(app.id, note), `Pay application ${app.number} returned.`)}>
            Return to contractor
          </button>
        </div>
      )}
    </li>
  );
}

function ChangeOrders({ data, cur, can, say, contractName }: Props) {
  const cos = data?.change_orders ?? [];
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  if (!cos.length) return null;
  return (
    <section aria-labelledby="changes">
      <h2 id="changes" className="text-[20px] font-semibold leading-tight">Change orders</h2>
      <ul className="mt-3 space-y-3">
        {cos.map((c) => {
          const open = c.stage === "pco" || c.stage === "submitted";
          const decide = open && can("change_order.approve");
          const value = amounts[c.id] ?? String(c.recommended_amount ?? c.proposed_amount);
          return (
            <li key={c.id} className={`rounded-md border border-rule bg-panel p-4 ${decide ? "border-l-[5px] border-l-hivis" : ""}`}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-display text-[18px] font-semibold text-ink">Change {c.number}: {c.title}</span>
                <StatusPill status={c.stage === "pco" ? "for_review" : c.stage} />
                <span className="ml-auto font-display text-[18px] font-semibold text-graphite">
                  {c.stage === "approved" ? `Approved ${money(c.approved_amount ?? 0, cur)}` : `Asked ${money(c.proposed_amount, cur)}`}
                </span>
              </div>
              <p className="mt-1 text-[14px] text-graphite/80">
                {contractName(c.contract_id)}
                {c.time_impact_days > 0 ? `, adds ${c.time_impact_days} days` : ", no time impact claimed"}
                {c.source_ref ? `, raised from ${c.source_ref.replace(":", " ")}` : ""}
              </p>
              {c.description && <p className="mt-2 max-w-[72ch] text-[14px] text-graphite">{c.description}</p>}
              {c.assessment && (
                <p className="mt-2 max-w-[72ch] border-l-2 border-ink/25 pl-3 text-[14px] text-graphite">
                  <span className="font-semibold text-ink">Assessment.</span> {c.assessment}
                  {c.recommended_amount != null && <> Recommended value {money(c.recommended_amount, cur)}.</>}
                </p>
              )}
              {decide && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <label className="text-[14px] text-graphite/80" htmlFor={`amt${c.id}`}>Approve at</label>
                  <input id={`amt${c.id}`} type="number" min={0} className="w-40 rounded border border-rule bg-panel px-2 py-1.5 text-right text-[14px]"
                         value={value} onChange={(e) => setAmounts({ ...amounts, [c.id]: e.target.value })} />
                  <button className={primary} onClick={async () => {
                    try { await api.decideChangeOrder(c.id, true, Number(value)); say(true, `Change ${c.number} approved.`); } catch (e) { say(false, errMsg(e)); }
                  }}>Approve change</button>
                  <button className={quiet} onClick={async () => {
                    try { await api.decideChangeOrder(c.id, false, null); say(true, `Change ${c.number} rejected.`); } catch (e) { say(false, errMsg(e)); }
                  }}>Reject</button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
