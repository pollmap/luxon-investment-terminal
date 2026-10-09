"use client";

import { useState } from "react";
import { restorePreferences, type Preferences, type Query } from "./types";
import {marketFetch} from "./public-transport";

export function saveFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ExportStocks({ query, codes, notify }: {query: Query; codes?: string[]; notify: (message: string) => void}) {
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true);
    try {
      const response = await marketFetch("/api/market/v1/screener/export", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({...query, ...(codes ? {codes} : {})}),
      });
      if (!response.ok) throw new Error("export");
      saveFile(await response.blob(), "주식담-종목목록.csv");
    } catch { notify("표를 내려받지 못했습니다. 다시 시도해 주세요."); }
    finally { setBusy(false); }
  }
  return <button className="mk-button" disabled={busy} onClick={download}>{busy ? "파일 준비 중…" : "CSV 내려받기"}</button>;
}

export default function PortablePreferences({ prefs, update, notify }: {
  prefs: Preferences; update: (patch: Partial<Preferences>) => void; notify: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function restore(file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > 1024 * 1024) throw new Error("size");
      const parsed = JSON.parse(await file.text());
      if (parsed?.format !== "korea-stocks-preferences" || parsed.version !== 1 ||
          !Array.isArray(parsed.preferences?.favorites) || !Array.isArray(parsed.preferences?.views)) throw new Error("format");
      const imported = restorePreferences(parsed.preferences);
      const favorites = [...new Set([...prefs.favorites, ...imported.favorites])].slice(0, 1000);
      const groups = {...prefs.groups};
      for (const [name, codes] of Object.entries(imported.groups))
        groups[name] = [...new Set([...(groups[name] || []), ...codes])].filter(c => favorites.includes(c));
      const views = [...prefs.views];
      for (const view of imported.views) {
        if (!views.some(v => v.id === view.id)) views.push(view);
      }
      update({favorites, groups, views: views.slice(0, 30)});
      notify("관심종목과 저장한 보기를 추가했습니다.");
    } catch { notify("올바른 관심종목 백업 파일을 선택해 주세요."); }
    finally { setBusy(false); }
  }
  return <div className="mk-preferences-transfer">
    <h3>관심종목 옮기기</h3>
    <p className="mk-caption">백업 파일로 다른 기기에 관심종목·그룹·저장한 보기를 추가할 수 있습니다.</p>
    <div className="mk-row">
      <button className="mk-button" onClick={() => saveFile(new Blob([JSON.stringify({
        format: "korea-stocks-preferences", version: 1,
        preferences: {favorites: prefs.favorites, groups: prefs.groups, views: prefs.views},
      }, null, 2)], {type: "application/json"}), "주식담-관심종목.json")}>백업 파일 저장</button>
      <label className="mk-button mk-file-control">{busy ? "가져오는 중…" : "백업 파일 가져오기"}
        <input aria-label="백업 파일 가져오기" type="file" accept=".json,application/json" disabled={busy} onChange={event => {
          void restore(event.target.files?.[0]); event.target.value = "";
        }} />
      </label>
    </div>
  </div>;
}
