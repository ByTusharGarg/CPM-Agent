"use client";

import { useEffect, useState } from "react";
import { api, errMsg, type ModelConfigItem } from "@/lib/api";

type Discovered = Awaited<ReturnType<typeof api.discoverModels>>;

const PROVIDER: Record<string, string> = { ollama: "Ollama (local)", anthropic: "Claude API", stub: "Offline placeholder" };

/** Organization admins choose which models the project chats can use. */
export function ModelSettings({ isAdmin }: { isAdmin: boolean }) {
  const [models, setModels] = useState<ModelConfigItem[]>([]);
  const [found, setFound] = useState<Discovered | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = () => api.models().then(setModels).catch((e) => setMsg({ ok: false, text: errMsg(e) }));
  useEffect(() => {
    load();
    if (isAdmin) api.discoverModels().then(setFound).catch(() => {});
  }, [isAdmin]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      setMsg({ ok: true, text: ok });
      load();
    } catch (e) {
      setMsg({ ok: false, text: errMsg(e) });
    }
  }

  const configured = new Set(models.map((m) => `${m.provider}:${m.model}`));
  const newOllama = (found?.ollama.models ?? []).filter((m) => !configured.has(`ollama:${m.model}`));
  const newClaude = (found?.anthropic.models ?? []).filter((m) => !configured.has(`anthropic:${m.model}`));

  return (
    <section id="models" aria-labelledby="models-h" className="mt-5 rounded-md border border-rule bg-panel p-5 text-[14px]">
      <h2 id="models-h" className="text-[20px] font-semibold leading-tight">Models</h2>
      <p className="mt-1 text-graphite/75">
        The model a project uses runs its project manager and specialist agents. People pick one under the chat box; projects without a choice use the default.
      </p>

      <ul className="mt-4 divide-y divide-rule/70 border-y border-rule/70">
        {models.map((m) => (
          <li key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5">
            <div className="min-w-[220px] flex-1">
              <div className="font-medium text-graphite">
                {m.label}
                {m.is_default && <span className="ml-2 rounded-sm bg-ink px-1.5 text-[12px] font-semibold text-white">Default</span>}
              </div>
              <div className="text-[13px] text-graphite/65">{PROVIDER[m.provider]}, {m.model}</div>
            </div>
            <div className={`text-[13px] ${m.available ? "text-site" : "text-brick"}`}>{m.available ? "Ready" : m.status}</div>
            {isAdmin && (
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-[13px]">
                  <input type="checkbox" checked={m.enabled} onChange={(e) => run(() => api.patchModel(m.id, { enabled: e.target.checked }), e.target.checked ? `${m.label} enabled.` : `${m.label} disabled.`)} />
                  Enabled
                </label>
                {!m.is_default && (
                  <button className="rounded border border-rule px-2 py-1 text-[13px] text-ink hover:bg-concrete" onClick={() => run(() => api.patchModel(m.id, { is_default: true }), `${m.label} is now the default.`)}>
                    Make default
                  </button>
                )}
                <button className="rounded px-2 py-1 text-[13px] text-graphite/60 hover:text-brick" onClick={() => run(() => api.deleteModel(m.id), `${m.label} removed.`)}>
                  Remove
                </button>
              </div>
            )}
          </li>
        ))}
        {models.length === 0 && <li className="py-3 text-graphite/70">No models configured; the server default is used.</li>}
      </ul>

      {isAdmin && found && (
        <div className="mt-4 space-y-3">
          <div>
            <div className="font-display text-[16px] font-semibold text-ink">Installed on this machine (Ollama)</div>
            {!found.ollama.reachable ? (
              <p className="text-brick">Ollama is not running at {found.ollama.url}. Start Ollama, then reload this page.</p>
            ) : newOllama.length === 0 ? (
              <p className="text-graphite/70">All installed Ollama models are configured. Pull another with <code>ollama pull &lt;model&gt;</code>.</p>
            ) : (
              <div className="mt-1 flex flex-wrap gap-2">
                {newOllama.map((m) => (
                  <button key={m.model} className="rounded border border-rule px-2.5 py-1 text-[13px] text-ink hover:bg-concrete"
                          onClick={() => run(() => api.addModel({ label: `${m.model} (local)`, provider: "ollama", model: m.model }), `${m.model} added.`)}>
                    Add {m.model} <span className="text-graphite/60">{m.size_gb} GB</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          {newClaude.length > 0 && (
            <div>
              <div className="font-display text-[16px] font-semibold text-ink">Claude</div>
              {!found.anthropic.key_present && <p className="text-graphite/70">Needs ANTHROPIC_API_KEY on the server before it can answer.</p>}
              <div className="mt-1 flex flex-wrap gap-2">
                {newClaude.map((m) => (
                  <button key={m.model} className="rounded border border-rule px-2.5 py-1 text-[13px] text-ink hover:bg-concrete"
                          onClick={() => run(() => api.addModel({ label: m.label, provider: "anthropic", model: m.model }), `${m.label} added.`)}>
                    Add {m.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {msg && <p className={`mt-3 ${msg.ok ? "text-site" : "text-brick"}`}>{msg.text}</p>}
    </section>
  );
}
