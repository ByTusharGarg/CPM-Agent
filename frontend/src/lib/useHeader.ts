"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Me, type Project, type ProjectHealth } from "./api";

/** Data for the site board on secondary project pages (schedule, documents, money). */
export function useHeader(pid: number) {
  const [project, setProject] = useState<Project | null>(null);
  const [health, setHealth] = useState<ProjectHealth | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    api.project(pid).then(setProject).catch(() => {});
    api.health(pid).then(setHealth).catch(() => {});
  }, [pid]);

  const perms = me?.projects.find((p) => p.id === pid)?.permissions;
  const can = useCallback((perm: string) => !!perms?.includes(perm), [perms]);
  return { project, health, me, can, role: me?.projects.find((p) => p.id === pid)?.role };
}
