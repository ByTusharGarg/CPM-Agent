"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
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
      <div className="w-full max-w-sm rounded-md border border-rule bg-panel p-6 shadow-sm">
        <div className="-mx-6 -mt-6 mb-6 rounded-t-md bg-ink px-6 pb-5 pt-6">
          <h1 className="font-display text-[30px] font-bold leading-none text-white">Site Manager</h1>
          <p className="mt-1.5 text-[14px] text-white/70">Sign in with a one-time code sent to your email.</p>
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
                className="mt-1 w-full rounded border border-rule px-3 py-2 text-sm outline-none focus:border-ink/50"
              />
            </label>
            <button disabled={busy} className="w-full rounded bg-ink py-2 text-sm font-medium text-white disabled:opacity-40">
              Send code
            </button>
          </form>
        ) : (
          <form onSubmit={verify} className="space-y-3">
            <p className="text-sm text-graphite/80">
              Code sent to <b>{email}</b>
            </p>
            <input
              inputMode="numeric"
              autoFocus
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="w-full rounded border border-rule px-3 py-2 text-center font-display text-lg tracking-[0.4em] outline-none focus:border-ink/50"
            />
            <button disabled={busy || code.length < 6} className="w-full rounded bg-ink py-2 text-sm font-medium text-white disabled:opacity-40">
              Sign in
            </button>
            <button type="button" onClick={() => { setStep("email"); setCode(""); setHint(""); }} className="w-full text-xs text-graphite/70 hover:text-ink">
              Use a different email
            </button>
          </form>
        )}
        {hint && <p className="mt-3 text-xs text-graphite/70">{hint}</p>}
        {err && <p className="mt-3 text-sm text-brick">{err}</p>}
        {step === "email" && (
          <div className="mt-5 border-t border-rule pt-4">
            <p className="mb-2 text-[13px] font-medium text-graphite/70">Demo accounts</p>
            <div className="flex flex-wrap gap-1.5">
              {DEMO.map((d) => (
                <button key={d} onClick={() => sendCode(undefined, d)} className="rounded-full border border-rule px-2.5 py-1 text-xs text-graphite/80 hover:bg-concrete">
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
