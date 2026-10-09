import { useEffect, useState } from "react";

// Tab-local navigation state. Guest watchlists and device preferences use their
// existing storage; these values only restore the list being explored.
export function useExplorationState<T>(key: string, fallback: T, validate: (value: unknown) => value is T) {
  const [value, setValue] = useState(fallback);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(key);
      const saved: unknown = raw ? JSON.parse(raw) : null;
      if (validate(saved)) setValue(saved);
    } catch { /* Browsing remains usable when session storage is unavailable. */ }
    setReady(true);
    // The key and validator are static at each call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => {
    if (!ready) return;
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Optional persistence. */ }
  }, [key, ready, value]);
  return [value, setValue, ready] as const;
}
