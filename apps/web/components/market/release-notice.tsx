"use client";

import {useEffect, useState} from "react";

const release = process.env.NEXT_PUBLIC_MARKET_RELEASE;

/** Check the static web release, not the independently deployed API version. */
export default function ReleaseNotice({onReload}: {onReload: () => void}) {
  const [available, setAvailable] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => {
    if (!release || release === "local") return;
    let active = true;
    let pending = false;
    const controller = new AbortController();
    async function check() {
      if (pending || document.hidden || !navigator.onLine) return;
      pending = true;
      const request = new AbortController();
      const timeout = setTimeout(() => request.abort(), 5000);
      const abort = () => request.abort();
      controller.signal.addEventListener("abort", abort, {once: true});
      try {
        const response = await fetch("/stocks/version.json", {cache: "no-store", signal: request.signal});
        if (!response.ok) return;
        const value: unknown = await response.json();
        const next = typeof value === "object" && value !== null && "release" in value ? value.release : null;
        if (active && typeof next === "string" && /^[a-zA-Z0-9-]{5,100}$/.test(next))
          setAvailable(next === release ? null : next);
      } catch { /* A failed update check must never hide market data. */ }
      finally {
        clearTimeout(timeout);
        controller.signal.removeEventListener("abort", abort);
        pending = false;
      }
    }
    void check();
    const timer = setInterval(() => void check(), 60_000);
    const visible = () => void check();
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("online", visible);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("online", visible);
    };
  }, []);
  if (!available || available === dismissed) return null;
  return <div className="mk-connection" role="status">
    <span>새 버전이 있어요.</span>
    <button onClick={onReload}>새로고침</button>
    <button onClick={() => setDismissed(available)}>나중에</button>
  </div>;
}
