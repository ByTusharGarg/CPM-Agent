"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { HardHat } from "lucide-react";
import { api, errMsg } from "@/lib/api";

const DEMO = ["owner@demo.local", "pm@demo.local", "consultant@demo.local", "viewer@demo.local", "site@apexsteel.example"];

function LoginForm() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/";
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [hint, setHint] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function sendCode(e?: React.FormEvent, addr = email) {
    e?.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.requestCode(addr);
      setEmail(addr);
      setStep("code");
      if (r.dev_code) {
        setCode(r.dev_code);
        setHint("Dev mode: the code was filled in for you.");
      } else setHint("If this email has an account, a 6-digit code is on its way.");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.verify(email, code);
      router.replace(next.startsWith("/") ? next : "/");
    } catch (e) {
      setErr(errMsg(e));
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-5">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500 text-white">
            <HardHat size={20} />
          </div>
          <div>
            <h1 className="font-semibold leading-tight">Site Manager</h1>
            <p className="text-xs text-slate-500">Sign in with a one-time code</p>
          </div>
        </div>
        {step === "email" ? (
          <form onSubmit={sendCode} className="space-y-3">
            <label className="block text-sm font-medium">
              Work email
              <input
                type="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-slate-400"
              />
            </label>
            <button disabled={busy} className="w-full rounded-lg bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-40">
              Send code
            </button>
          </form>
        ) : (
          <form onSubmit={verify} className="space-y-3">
            <p className="text-sm text-slate-600">
              Code sent to <b>{email}</b>
            </p>
            <input
              inputMode="numeric"
              autoFocus
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-center font-mono text-lg tracking-[0.4em] outline-none focus:border-slate-400"
            />
            <button disabled={busy || code.length < 6} className="w-full rounded-lg bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-40">
              Sign in
            </button>
            <button type="button" onClick={() => { setStep("email"); setCode(""); setHint(""); }} className="w-full text-xs text-slate-500 hover:text-slate-800">
              Use a different email
            </button>
          </form>
        )}
        {hint && <p className="mt-3 text-xs text-slate-500">{hint}</p>}
        {err && <p className="mt-3 text-sm text-red-600">{err}</p>}
        {step === "email" && (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">Demo accounts</p>
            <div className="flex flex-wrap gap-1.5">
              {DEMO.map((d) => (
                <button key={d} onClick={() => sendCode(undefined, d)} className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">
                  {d}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
