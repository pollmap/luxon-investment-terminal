"use client";

import { useEffect, useState } from "react";
import { format, request, tone } from "./types";
import CompanyLogo from "./company-logo";

type Comparison = {
  status: string;
  start: string | null;
  end: string | null;
  pending: string[];
  series: {code: string; name: string; logo_url?: string; points: {date: string; value: number}[]}[];
};
const colors = ["var(--mk-accent)", "var(--mk-up)", "var(--mk-down)", "var(--mk-muted)"];

export default function ComparisonChart({ codes }: { codes: string[] }) {
  const joined = codes.join(",");
  const [period, setPeriod] = useState("year");
  const [data, setData] = useState<Comparison | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData(null);
    setCursor(null);
    setError(false);
    async function load() {
      try {
        const result = await request<Comparison>(`/compare?codes=${encodeURIComponent(joined)}&period=${period}`, {signal: abort.signal});
        if (abort.signal.aborted) return;
        setData(result);
        setError(false);
        if (result.pending.length) timer = setTimeout(load, 5000);
      } catch {
        if (!abort.signal.aborted) setError(true);
      }
    }
    void load();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [joined, period, retry]);
  const points = data?.series[0]?.points || [];
  const count = points.length;
  const active = Math.min(cursor ?? count - 1, count - 1);
  const values = data?.series.flatMap(s => s.points.map(p => p.value)) || [];
  const low = Math.min(0, ...values), high = Math.max(1, ...values);
  const spread = Math.max(high - low, 1);
  const x = (i: number) => 58 + i / Math.max(1, count - 1) * 714;
  const y = (v: number) => 18 + (high - v) / spread * 218;
  return <section className="mk-comparison-chart" aria-label="주가 수익률 비교">
    <div className="mk-section-head">
      <h2>주가 수익률 비교</h2>
      <div className="mk-segment" aria-label="비교 기간">
        {[["month", "1개월"], ["quarter", "3개월"], ["year", "1년"], ["five_years", "5년"]].map(([key, label]) =>
          <button key={key} aria-pressed={period === key} onClick={() => setPeriod(key)}>{label}</button>)}
      </div>
    </div>
    {count > 1 ? <>
      <div className="mk-comparison-legend">
        {data!.series.map((s, i) => <div key={s.code}>
          <span className="mk-series-dot" style={{background: colors[i]}} />
          <CompanyLogo url={s.logo_url} name={s.name} />
          <span>{s.name}</span>
          <strong className={tone(s.points[active].value)}>{format(s.points[active].value, "change")}</strong>
        </div>)}
      </div>
      <div className="mk-comparison-scale"><span>최저 {low.toFixed(1)}%</span><span>최고 {high.toFixed(1)}%</span></div>
      <svg viewBox="0 0 800 248" role="img" aria-label={`${data!.start}부터 ${data!.end}까지 주가 수익률 비교`} onPointerMove={event => {
        const bounds = event.currentTarget.getBoundingClientRect();
        setCursor(Math.max(0, Math.min(count - 1, Math.round(((event.clientX - bounds.left) / bounds.width * 800 - 58) / 714 * (count - 1)))));
      }} onPointerLeave={() => setCursor(null)}>
        {[high, (high + low) / 2, low].map((value, i) => <g key={i}>
          <line x1="58" x2="772" y1={y(value)} y2={y(value)} stroke="var(--mk-line)" />
          <text x="50" y={y(value) + 4} textAnchor="end" fill="var(--mk-muted)" fontSize="12">{value.toFixed(1)}%</text>
        </g>)}
        <line x1="58" x2="772" y1={y(0)} y2={y(0)} stroke="var(--mk-muted)" strokeDasharray="3 4" />
        {data!.series.map((s, i) => <polyline key={s.code} points={s.points.map((p, n) => `${x(n).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")} fill="none" stroke={colors[i]} strokeWidth="2.5" strokeDasharray={i === 3 ? "6 3" : undefined} />)}
        <line x1={x(active)} x2={x(active)} y1="18" y2="236" stroke="var(--mk-muted)" strokeDasharray="3 4" />
      </svg>
      <div className="mk-comparison-dates"><span>{data!.start}</span><span>{data!.end}</span></div>
      <label className="mk-comparison-cursor">{points[active]?.date}
        <input type="range" aria-label="비교 차트 날짜" min="0" max={count - 1} value={active} onChange={event => setCursor(Number(event.target.value))} />
      </label>
      <p className="mk-caption">같은 시작일을 0%로 비교 · 배당 제외</p>
    </> : <div className="mk-empty" role="status">{error ? "비교 차트를 불러오지 못했습니다." : data?.status === "no_overlap" ? "선택한 종목에 겹치는 거래일이 없습니다." : "선택한 종목의 차트를 준비하고 있습니다."}</div>}
    {data?.pending.length ? <p role="status" className="mk-caption">{data.pending.length}개 종목의 차트를 추가로 불러오는 중…</p> : null}
    {error && <button className="mk-button" onClick={() => setRetry(v => v + 1)}>다시 불러오기</button>}
  </section>;
}
