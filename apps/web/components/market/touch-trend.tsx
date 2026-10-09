"use client";
import {useState} from "react";
import {format, tone} from "./types";

export type TrendPoint = {date: string; value: number | null};
export function TouchTrend({points, label, unit, signed = false}: {points: TrendPoint[]; label: string; unit: string; signed?: boolean}) {
  const [cursor, setCursor] = useState<number | null>(null);
  const index = Math.max(0, Math.min(cursor ?? points.length - 1, points.length - 1));
  const selected = points[index];
  const valid = points.flatMap(point => point.value == null ? [] : [point.value]);
  if (!points.length || !valid.length) return <p className="mk-empty">표시할 {label} 자료가 없습니다.</p>;
  const low = Math.min(0, ...valid), high = Math.max(1, ...valid), span = high - low;
  const x = (i: number) => 12 + i / Math.max(1, points.length - 1) * 336;
  const y = (v: number) => 12 + (high - v) / span * 146;
  const segments: string[][] = [[]];
  points.forEach((point, i) => { if (point.value == null) segments.push([]); else segments.at(-1)!.push(`${x(i)},${y(point.value)}`); });
  const valueText = (value: number | null) => value == null ? "—" : `${signed && value > 0 ? "+" : ""}${format(value, unit === "%" ? "foreign" : "number")}${unit === "%" ? "" : unit}`;
  return <div className="mk-touch-trend">
    <div className="mk-trend-reading"><span>{selected?.date}</span><strong className={signed ? tone(selected?.value) : ""}>{valueText(selected?.value ?? null)}</strong></div>
    <div className="mk-trend-scale"><span>{valueText(low)}</span><span>{valueText(high)}</span></div>
    <svg viewBox="0 0 360 176" role="img" aria-label={`${label} 추이`} onClick={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      setCursor(Math.max(0, Math.min(points.length - 1, Math.round(((event.clientX - rect.left) / rect.width * 360 - 12) / 336 * (points.length - 1)))));
    }}>
      <line x1="12" x2="348" y1={y(0)} y2={y(0)} stroke="var(--mk-line)" />
      {segments.filter(segment => segment.length > 1).map((segment, i) => <polyline key={i} points={segment.join(" ")} stroke="var(--mk-accent)" strokeWidth="2.5" fill="none"/>)}
      {points.map((point, i) => point.value != null ? <circle key={point.date} cx={x(i)} cy={y(point.value)} r={index === i ? 4 : 1.5} fill="var(--mk-accent)"/> : null)}
      <line x1={x(index)} x2={x(index)} y1="8" y2="164" stroke="var(--mk-muted)" strokeDasharray="3 4" />
    </svg>
    <div className="mk-trend-dates"><span>{points[0].date}</span><span>{points.at(-1)!.date}</span></div>
    <div className="mk-trend-controls"><button aria-label={`${label} 이전 날짜`} disabled={index === 0} onClick={() => setCursor(index - 1)}>이전</button><input type="range" aria-label={`${label} 날짜`} min="0" max={points.length - 1} value={index} onChange={event => setCursor(Number(event.target.value))}/><button aria-label={`${label} 다음 날짜`} disabled={index === points.length - 1} onClick={() => setCursor(index + 1)}>다음</button></div>
  </div>;
}

