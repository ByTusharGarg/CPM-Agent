"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  API,
  errMsg,
  api,
  type ActionEntry,
  type Alert,
  type ChatMessage,
  type Policy,
  type Project,
  type ProjectHealth,
  type Report,
  type WorkItem,
} from "./api";

export interface Activity {
  key: number;
  actor: string;
  tool: string;
  at: number;
}

export interface StreamingReply {
  id: string;
  text: string;
}

/** All live state for one project, kept fresh by the SSE stream. */
export function useProject(projectId: number, onProjectsChanged?: () => void) {
  const [project, setProject] = useState<Project | null>(null);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [health, setHealth] = useState<ProjectHealth | null>(null);
  const [needs, setNeeds] = useState<{ alerts: Alert[]; approvals: ActionEntry[] }>({ alerts: [], approvals: [] });
  const [work, setWork] = useState<WorkItem[]>([]);
  const [observed, setObserved] = useState<ActionEntry[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [streaming, setStreaming] = useState<StreamingReply | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [routerBusy, setRouterBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actKey = useRef(0);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const debounce = useCallback((key: string, fn: () => void, ms = 250) => {
    clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(fn, ms);
  }, []);

  const safe = useCallback(<T,>(p: Promise<T>, set: (v: T) => void) => {
    p.then(set).catch((e) => setError(errMsg(e)));
  }, []);

  const refreshHealth = useCallback(() => safe(api.health(projectId), setHealth), [projectId, safe]);
  const refreshNeeds = useCallback(() => {
    safe(api.needsYou(projectId), setNeeds);
    onProjectsChanged?.();
  }, [projectId, safe, onProjectsChanged]);
  const refreshWork = useCallback(() => safe(api.workItems(projectId), setWork), [projectId, safe]);
  const refreshObserved = useCallback(() => {
    safe(api.actions(projectId), setObserved);
    safe(api.policies(projectId), setPolicies);
  }, [projectId, safe]);
  const refreshReports = useCallback(() => safe(api.reports(projectId), setReports), [projectId, safe]);

  useEffect(() => {
    setProject(null);
    setChat([]);
    setHealth(null);
    setStreaming(null);
    setActivity([]);
    setError(null);
    safe(api.project(projectId), setProject);
    safe(api.chat(projectId), setChat);
    refreshHealth();
    refreshNeeds();
    refreshWork();
    refreshObserved();
    refreshReports();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const es = new EventSource(`${API}/api/projects/${projectId}/stream`, { withCredentials: true });
    es.addEventListener("hello", () => setConnected(true));
    es.onerror = () => setConnected(false);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const on = (type: string, fn: (d: any) => void) =>
      es.addEventListener(type, (e) => fn(JSON.parse((e as MessageEvent).data)));

    on("chat", (m: ChatMessage) => {
      setChat((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
      if (m.meta?.reply_id) setStreaming((s) => (s && s.id === m.meta.reply_id ? null : s));
      if (m.kind === "alert" || m.kind === "approval") refreshNeeds();
    });
    on("reply_start", (d) => setStreaming({ id: d.reply_id, text: "" }));
    on("delta", (d) => setStreaming((s) => (s && s.id === d.reply_id ? { ...s, text: s.text + d.text } : { id: d.reply_id, text: d.text })));
    on("reply_end", (d) => setStreaming((s) => (s && s.id === d.reply_id ? null : s)));
    on("activity", (d) => {
      actKey.current += 1;
      const item = { key: actKey.current, actor: d.actor, tool: d.tool, at: Date.now() };
      setActivity((prev) => [...prev.slice(-5), item]);
    });
    on("router", (d) => setRouterBusy(!!d.busy));
    on("work_item", () => debounce("work", refreshWork));
    on("kpi", () => debounce("kpi", () => { refreshHealth(); onProjectsChanged?.(); }, 400));
    on("alerts", () => debounce("alerts", refreshNeeds));
    on("observe", () => debounce("obs", refreshObserved));
    on("report", () => debounce("rep", refreshReports));
    return () => es.close();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // fade old activity chips
  useEffect(() => {
    const t = setInterval(() => setActivity((prev) => prev.filter((a) => Date.now() - a.at < 20000)), 3000);
    return () => clearInterval(t);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const { message } = await api.send(projectId, text);
      setChat((prev) => (prev.some((x) => x.id === message.id) ? prev : [...prev, message]));
    },
    [projectId],
  );

  const can = useCallback((perm: string) => !!project?.permissions?.includes(perm), [project]);

  return {
    can, project, chat, health, needs, work, observed, policies, reports, streaming, activity, routerBusy, connected, error,
    setError, send, refreshNeeds, refreshObserved, refreshWork, refreshHealth, refreshReports,
  };
}

export type ProjectState = ReturnType<typeof useProject>;
