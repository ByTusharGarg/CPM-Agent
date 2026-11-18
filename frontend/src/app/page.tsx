"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, errMsg } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "empty" | "error">("loading");
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .projects()
      .then((ps) => {
        if (ps.length) router.replace(ps[0].role === "contractor" ? `/projects/${ps[0].id}/updates` : `/projects/${ps[0].id}`);
        else setState("empty");
      })
      .catch((e) => {
        setErr(errMsg(e));
        setState("error");
      });
  }, [router]);

  return (
    <main className="flex h-full items-center justify-center p-6">
      {state === "loading" && <p className="text-graphite/70">Loading projects…</p>}
      {state === "empty" && (
        <div className="text-center">
          <h1 className="text-xl font-semibold">No projects yet</h1>
          <p className="mt-2 text-graphite/70">Create your first project to start the manager agent.</p>
          <Link href="/projects/new" className="mt-4 inline-block rounded bg-ink px-4 py-2 text-white">
            New project
          </Link>
        </div>
      )}
      {state === "error" && (
        <div className="max-w-md text-center">
          <h1 className="text-xl font-semibold">Can’t reach the backend</h1>
          <p className="mt-2 text-sm text-graphite/70">{err}</p>
          <p className="mt-3 text-sm text-graphite/70">
            Start it with <code className="rounded bg-concrete px-1">uvicorn app.main:app --reload</code> in{" "}
            <code className="rounded bg-concrete px-1">backend/</code>.
          </p>
        </div>
      )}
    </main>
  );
}
