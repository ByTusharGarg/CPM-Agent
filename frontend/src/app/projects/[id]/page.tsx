"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, type Me, type ProjectSummary } from "@/lib/api";
import { useProject } from "@/lib/useProject";
import { Sidebar } from "@/components/Sidebar";
import { ChatPane } from "@/components/ChatPane";
import { RightPanel } from "@/components/RightPanel";

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const pid = Number(params.id);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [me, setMe] = useState<Me | null>(null);

  const loadProjects = useCallback(() => {
    api.projects().then(setProjects).catch(() => {});
  }, []);
  useEffect(loadProjects, [loadProjects]);
  useEffect(() => {
    api.me().then((m) => {
      setMe(m);
      const mine = m.projects.find((p) => p.id === pid);
      if (mine?.role === "contractor") router.replace(`/projects/${pid}/updates`);
    }).catch(() => {});
  }, [pid, router]);

  const state = useProject(pid, loadProjects);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar projects={projects} activeId={pid} me={me} />
      <ChatPane state={state} />
      <RightPanel state={state} />
    </div>
  );
}
