"use client";
import { useState } from "react";
import CompanyLogo from "./company-logo";
import { Star } from "lucide-react";
import { labels, format, tone, type MetricId, type Stock } from "./types";
export default function MobileList({
  rows,
  favorites,
  selected,
  selectionMode: controlledSelection,
  metric,
  onOpen,
  onFavorite,
  onSelect,
  onMetric,
}: {
  rows: Stock[];
  favorites: string[];
  selected: string[];
  selectionMode?: boolean;
  metric: MetricId;
  onOpen: (code: string) => void;
  onFavorite: (code: string) => void;
  onSelect: (code: string) => void;
  onMetric: (metric: MetricId, row?: Stock) => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const selectionMode = controlledSelection ?? (choosing || selected.length > 0);
  return (
    <div className="mk-mobile-list">
      {controlledSelection === undefined && <button className="mk-button mk-compare-mode" aria-pressed={selectionMode} onClick={() => {
        if (selectionMode) selected.forEach(onSelect);
        setChoosing(!selectionMode);
      }}>{selectionMode ? "비교 선택 취소" : "비교할 종목 선택"}</button>}
      <div className="mk-mobile-list-head">
        <span>종목 / 현재가</span>
        <button onClick={() => onMetric(metric)}>{labels[metric]} 뜻 보기</button>
      </div>
      {rows.map((s) => (
        <article className="mk-mobile-stock" key={s.code}>
          {selectionMode && <label className="mk-mobile-select">
            <input
              aria-label={`${s.name} 비교 선택`}
              type="checkbox"
              checked={selected.includes(s.code)}
              disabled={selected.length >= 4 && !selected.includes(s.code)}
              onChange={() => onSelect(s.code)}
            />
          </label>}
          <div className="mk-mobile-stock-body mk-mobile-compact-row">
              <button className="mk-mobile-stock-open"
                aria-label={`${s.name} ${s.code} ${s.market}`}
                data-code={s.code}
                onClick={() => onOpen(s.code)}
              >
                <CompanyLogo url={s.logo_url} name={s.name}/><span className="mk-mobile-company-text"><strong>{s.name}</strong>
                <small>{labels[metric]} {format(s[metric], metric)}</small></span>
              <span className="mk-mobile-stock-price">
                <b>{format(s.price)}</b>
                <small className={tone(s.change)}>
                  {format(s.change, "change")}
                </small>
              </span></button>
              <button
                className={`mk-star ${favorites.includes(s.code) ? "is-saved" : ""}`}
                aria-label={`${s.name} 관심종목 ${favorites.includes(s.code) ? "해제" : "추가"}`}
                aria-pressed={favorites.includes(s.code)}
                onClick={() => onFavorite(s.code)}
              >
                <Star size={20} />
              </button>
          </div>
        </article>
      ))}
    </div>
  );
}
