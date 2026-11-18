"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, errMsg, type ModelConfigItem, type ProjectModel } from "@/lib/api";

/** Which model answers in this project's chat (and runs its agents). */
export function ModelPicker({ projectId, canChange }: { projectId: number; canChange: boolean }) {
  const [models, setModels] = useState<ModelConfigItem[]>([]);
  const [current, setCurrent] = useState<ProjectModel | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.models().then(setModels).catch(() => {});
    api.projectModel(projectId).then(setCurrent).catch(() => {});
  }, [projectId]);

  async function choose(value: string) {
    setErr("");
    try {
      setCurrent(await api.setProjectModel(projectId, value === "" ? null : Number(value)));
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  if (!current) return null;
  const enabled = models.filter((m) => m.enabled);
  const orgDefault = enabled.find((m) => m.is_default);

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-graphite/80">
      <label htmlFor={`model-${projectId}`}>Answering with</label>
      {canChange ? (
        <select
          id={`model-${projectId}`}
          value={current.model_config_id ?? ""}
          onChange={(e) => choose(e.target.value)}
          className="rounded border border-rule bg-panel px-2 py-1 text-[13px] font-medium text-ink focus:border-ink"
        >
          <option value="">{orgDefault ? `Default: ${orgDefault.label}` : "Server default"}</option>
          {enabled.map((m) => (
            <option key={m.id} value={m.id} disabled={!m.available}>
              {m.label}{m.available ? "" : ` (${m.status})`}
            </option>
          ))}
        </select>
      ) : (
        <span className="font-medium text-ink">{current.label}</span>
      )}
      {!current.available && <span className="text-brick">{current.status}</span>}
      {err && <span className="text-brick">{err}</span>}
      <Link href="/settings#models" className="text-graphite/60 underline underline-offset-2 hover:text-ink">
        Manage models
      </Link>
    </div>
  );
}
