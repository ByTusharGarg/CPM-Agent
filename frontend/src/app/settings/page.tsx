"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { api, errMsg, timeAgo, type Me, type NotificationPrefs, type OutboxItem } from "@/lib/api";
import { ModelSettings } from "@/components/ModelSettings";
import { StatusPill } from "@/components/ui";

const HOURS = Array.from({ length: 24 }, (_, i) => i);

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [p, setP] = useState<NotificationPrefs | null>(null);
  const [outbox, setOutbox] = useState<OutboxItem[]>([]);
  const [msg, setMsg] = useState("");
  const [profile, setProfile] = useState({ name: "", phone: "" });
  const [profileMsg, setProfileMsg] = useState("");
  const [status, setStatus] = useState<Awaited<ReturnType<typeof api.channelStatus>> | null>(null);

  useEffect(() => {
    api.me().then((m) => { setMe(m); setProfile({ name: m.name, phone: m.phone }); }).catch(() => {});
    api.channelStatus().then(setStatus).catch(() => {});
    api.prefs().then(setP).catch((e) => setMsg(errMsg(e)));
    api.outbox().then(setOutbox).catch(() => {});
  }, []);

  async function saveProfile() {
    try {
      await api.updateMe(profile);
      setProfileMsg("Saved");
    } catch (e) {
      setProfileMsg(errMsg(e));
    }
  }

  async function save() {
    if (!p) return;
    try {
      await api.savePrefs(p);
      setMsg("Saved");
    } catch (e) {
      setMsg(errMsg(e));
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-5 py-8">
      <Link href="/" className="flex items-center gap-1 text-sm text-graphite/70 hover:text-ink">
        <ArrowLeft size={14} /> Back
      </Link>
      <h1 className="mt-4 text-xl font-semibold">Settings</h1>
      <p className="text-sm text-graphite/70">
        {me ? `${me.name || me.email} (${me.email}${me.phone ? `, ${me.phone}` : ""})` : "…"}
      </p>
      <p className="mt-2 text-sm text-graphite/80">
        The manager only interrupts you for decisions. Choose what reaches you outside the app and when; everything else is bundled into one daily digest.
      </p>

      <div className="mt-5 space-y-3 rounded-md border border-rule bg-panel p-5 text-sm">
        <div className="font-medium">Profile</div>
        <div className="flex flex-wrap gap-2">
          <input value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} placeholder="Name" className="flex-1 rounded-md border border-rule px-2 py-1.5" />
          <input value={profile.phone} onChange={(e) => setProfile({ ...profile, phone: e.target.value })} placeholder="WhatsApp number, e.g. +919800000001" className="flex-1 rounded-md border border-rule px-2 py-1.5" />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={saveProfile} className="rounded border border-rule px-3 py-1.5 text-sm font-medium hover:bg-concrete">Save profile</button>
          {profileMsg && <span className="text-graphite/70">{profileMsg}</span>}
        </div>
        <p className="text-xs text-graphite/70">
          Your WhatsApp number identifies you when you message the project WhatsApp number: contractors send daily reports, owners ask questions and answer decision requests.
        </p>
      </div>

      {p && (
        <div className="mt-5 space-y-4 rounded-md border border-rule bg-panel p-5 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={p.email_enabled} onChange={(e) => setP({ ...p, email_enabled: e.target.checked })} /> Email
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={p.whatsapp_enabled} onChange={(e) => setP({ ...p, whatsapp_enabled: e.target.checked })} /> WhatsApp
            {!profile.phone && <span className="text-xs text-[#7a5a00]">add your number above</span>}
            {status && !status.whatsapp.outbound && <span className="text-xs text-graphite/50">(WhatsApp is not connected on the server yet)</span>}
          </label>
          <label className="block">
            Send immediately when severity is at least
            <select
              value={p.immediate_min_severity}
              onChange={(e) => setP({ ...p, immediate_min_severity: e.target.value as NotificationPrefs["immediate_min_severity"] })}
              className="ml-2 rounded-md border border-rule px-2 py-1"
            >
              <option value="info">info (everything)</option>
              <option value="warning">warning</option>
              <option value="critical">critical only</option>
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            Quiet hours (critical still gets through):
            <select value={p.quiet_start_hour ?? ""} onChange={(e) => setP({ ...p, quiet_start_hour: e.target.value === "" ? null : Number(e.target.value) })} className="rounded-md border border-rule px-2 py-1">
              <option value="">off</option>
              {HOURS.map((h) => <option key={h} value={h}>{`${h}:00`}</option>)}
            </select>
            to
            <select value={p.quiet_end_hour ?? ""} onChange={(e) => setP({ ...p, quiet_end_hour: e.target.value === "" ? null : Number(e.target.value) })} className="rounded-md border border-rule px-2 py-1">
              <option value="">off</option>
              {HOURS.map((h) => <option key={h} value={h}>{`${h}:00`}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={p.digest_enabled} onChange={(e) => setP({ ...p, digest_enabled: e.target.checked })} /> Daily digest of everything else
          </label>
          <div className="flex items-center gap-3">
            <button onClick={save} className="rounded bg-ink px-4 py-2 text-sm font-medium text-white">Save</button>
            {msg && <span className="text-sm text-graphite/70">{msg}</span>}
          </div>
        </div>
      )}

      <ModelSettings isAdmin={!!me?.is_org_admin} />

      {status && (
        <div className="mt-5 rounded-md border border-rule bg-panel p-5 text-sm">
          <div className="mb-2 font-medium">Channels on this server</div>
          <ul className="space-y-1 text-graphite/80">
            <li>Email - sending: {status.email.outbound ? "on" : "off (set CPM_SMTP_HOST)"}; replies: {status.email.inbound ? "on" : "off (set CPM_EMAIL_INBOUND_SECRET)"}</li>
            <li>WhatsApp - sending: {status.whatsapp.outbound ? "on" : "off (set CPM_WA_TOKEN / CPM_WA_PHONE_NUMBER_ID)"} · receiving: {status.whatsapp.inbound ? "on" : "off (set CPM_WA_APP_SECRET / CPM_WA_VERIFY_TOKEN)"} · re-engagement template: {status.whatsapp.template ? "on" : "off"}</li>
            <li>Voice notes: {status.transcription === "none" ? "stored, not transcribed" : `transcribed (${status.transcription})`}</li>
          </ul>
        </div>
      )}

      <h2 className="mt-8 text-sm font-semibold">Recent notifications</h2>
      {outbox.length === 0 ? (
        <p className="mt-2 text-sm text-graphite/70">Nothing sent yet.</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {outbox.map((n) => (
            <li key={n.id} className="flex items-center gap-2 rounded border border-rule bg-panel px-3 py-2 text-sm">
              <StatusPill status={n.status} />
              <span className="text-xs text-graphite/70">{n.channel}</span>
              <span className="flex-1 truncate">{n.title}</span>
              <span className="text-xs text-graphite/50" title={n.error}>{timeAgo(n.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
