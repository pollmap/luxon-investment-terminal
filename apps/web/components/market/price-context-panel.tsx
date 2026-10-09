"use client";
import {marketFetch} from "./public-transport";
import {memo, useEffect, useId, useState} from "react";
import Dialog from "./dialog";
import {API, format, tone} from "./types";

type View = "return" | "drawdown" | "monthly";
type Selection = {view: View; years: number; day: string};
type Point = {date: string; close: number; peak: number; peak_date: string; return_pct: number; drawdown_pct: number; gap_before: boolean};
type Month = {month: string; return_pct: number | null; start: string | null; end: string | null; start_close: number | null; end_close: number | null; status: string; as_of_month: boolean};
type Context = {
  code: string; status: string; revision: string; series: Point[]; months: Month[];
  summary: {start: string; end: string; observations: number; return_pct: number; drawdown_pct: number; max_drawdown_pct: number; peak_date: string; trough_date: string};
  basis: {venue: string | null; adjustment: string | null; unit: string};
  availability: {prices: boolean; reason: string | null};
  source_trace?: {source?: string; source_url?: string};
};
const views: [View, string][] = [["return", "가격 변화"], ["drawdown", "고점 대비"], ["monthly", "월별 변화"]];
const defaults: Selection = {view: "return", years: 1, day: ""};
const isDay = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
function clean(value: Partial<Selection>): Selection {
  return {view: views.some(([id]) => id === value.view) ? value.view! : "return",
    years: [0, 1, 3, 5, 10].includes(Number(value.years)) ? Number(value.years) : 1,
    day: isDay(value.day) ? value.day : ""};
}
const percent = (value: number | null | undefined) => format(value, "change");

/** Per-security state; shared links override only explicitly selected fields. */
export default memo(function PriceContextPanel({code, name}: {code: string; name: string}) {
  const [selection, setSelection] = useState(defaults);
  const [open, setOpen] = useState(false), [ready, setReady] = useState(false);
  const [result, setResult] = useState<{key: string; data: Context} | null>(null);
  const [error, setError] = useState(""), [retry, setRetry] = useState(0);
  const [help, setHelp] = useState(false), [notice, setNotice] = useState("");
  const [monthYear, setMonthYear] = useState<string | null>(null);
  const bodyId = useId(), plotId = useId();
  const requestKey = `${code}:${selection.years}`;
  const data = result?.key === requestKey ? result.data : null;
  const points = data?.series ?? [];
  const found = selection.day ? points.reduce((latest, p, i) => p.date <= selection.day ? i : latest, -1) : points.length - 1;
  const index = Math.max(0, Math.min(found < 0 ? 0 : found, points.length - 1));
  const selected = points[index];

  useEffect(() => {
    let saved = defaults;
    try { saved = clean(JSON.parse(localStorage.getItem(`stocks.price-context.${code}`) || "{}")); } catch { /* Private storage can be unavailable. */ }
    const query = new URLSearchParams(location.search);
    if (query.get("code") === code) {
      const linked: Partial<Selection> = {};
      if (views.some(([id]) => id === query.get("insight"))) linked.view = query.get("insight") as View;
      if (query.has("insight_years")) linked.years = Number(query.get("insight_years"));
      if (query.has("insight_date")) linked.day = query.get("insight_date") || "";
      saved = clean({...saved, ...linked}); setOpen(Boolean(linked.view));
    }
    setSelection(saved); setReady(true);
  }, [code]);

  useEffect(() => {
    if (!open || !ready) return;
    const controller = new AbortController();
    let busy = false, etag = "";
    async function load() {
      if (busy || document.hidden) return;
      busy = true;
      try {
        const response = await marketFetch(`${API}/securities/${code}/analysis/price-context?years=${selection.years}`, {
          signal: controller.signal, cache: "no-store", headers: etag ? {"If-None-Match": etag} : {},
        });
        if (response.status === 304) { setError(""); return; }
        if (!response.ok) throw new Error();
        const next: Context = await response.json();
        if (controller.signal.aborted) return;
        if (next.code !== code) throw new Error();
        etag = response.headers.get("ETag") || "";
        setResult({key: requestKey, data: next}); setError("");
      } catch { if (!controller.signal.aborted) setError("가격 분석을 불러오지 못했습니다."); }
      finally { busy = false; }
    }
    setError(""); void load();
    const timer = setInterval(load, 60000);
    document.addEventListener("visibilitychange", load);
    return () => { controller.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [code, open, ready, requestKey, selection.years, retry]);

  function writeLink(next: Selection, expanded = true) {
    const url = new URL(location.href);
    if (url.searchParams.get("code") !== code) return;
    if (expanded) url.searchParams.set("insight", next.view); else url.searchParams.delete("insight");
    url.searchParams.set("insight_years", String(next.years));
    if (next.day) url.searchParams.set("insight_date", next.day); else url.searchParams.delete("insight_date");
    history.replaceState(history.state, "", url);
    window.dispatchEvent(new Event("market-route-change"));
  }
  function update(patch: Partial<Selection>) {
    const next = clean({...selection, ...patch});
    setSelection(next); setNotice("");
    try { localStorage.setItem(`stocks.price-context.${code}`, JSON.stringify(next)); } catch { /* Controls work without persistence. */ }
    writeLink(next);
  }
  function toggle() { setOpen(!open); writeLink(selection, !open); }
  function chooseView(view: View) {
    // Dialog closes its own history entry. Apply the chart URL to the parent
    // entry only after that popstate, so closing help does not undo the choice.
    if (history.state?.__marketDialog) {
      window.addEventListener("popstate", () => update({view}), {once: true});
    } else update({view});
    setHelp(false);
  }
  async function share() {
    try {
      const url = new URL(location.href);
      // Share the view only; never include watchlists or unrelated private state.
      url.search = new URLSearchParams({code, tab: "요약", insight: selection.view,
        insight_years: String(selection.years), ...(selection.day ? {insight_date: selection.day} : {})}).toString();
      await navigator.clipboard.writeText(url.toString()); setNotice("현재 차트의 링크를 복사했습니다.");
    } catch { setNotice("링크를 복사하지 못했습니다. 주소창의 링크를 복사해 주세요."); }
  }
  function exportCsv() {
    if (!data) return;
    const rows = selection.view === "monthly"
      ? [["월", "시작 관측일", "마지막 관측일", "시작 종가(원)", "마지막 종가(원)", "가격 변화(%)"], ...data.months.map(m => [m.month, m.start, m.end, m.start_close, m.end_close, m.return_pct])]
      : [["날짜", "종가(원)", "기간 가격 변화(%)", "이전 최고 종가(원)", "최고 종가 날짜", "고점 대비(%)"], ...points.map(p => [p.date, p.close, p.return_pct, p.peak, p.peak_date, p.drawdown_pct])];
    const csv = rows.map(row => row.map(cell => `"${String(cell ?? "").replaceAll('"', '""')}"`).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff" + csv], {type: "text/csv;charset=utf-8"}));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${code}-${selection.view}-observed-prices.csv`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const monthlyYears = [...new Set(data?.months.map(m => m.month.slice(0, 4)) ?? [])].reverse();
  const activeYear = monthYear && monthlyYears.includes(monthYear) ? monthYear : monthlyYears[0];
  const months = data?.months.filter(m => m.month.startsWith(activeYear)) ?? [];
  const activeMonth = data?.months.find(m => m.month === selected?.date.slice(0, 7));
  const value = (p: Point) => selection.view === "drawdown" ? p.drawdown_pct : p.return_pct;
  const numbers = points.map(value), low = Math.min(0, ...numbers), high = Math.max(0, ...numbers), span = Math.max(1, high - low);
  const firstTime = points.length ? Date.parse(points[0].date) : 0;
  const timeSpan = points.length ? Math.max(1, Date.parse(points.at(-1)!.date) - firstTime) : 1;
  const x = (p: Point) => 16 + (Date.parse(p.date) - firstTime) / timeSpan * 768;
  const y = (v: number) => 16 + (high - v) / span * 188;
  const segments: Point[][] = [[]];
  points.forEach(p => { if (p.gap_before) segments.push([]); segments.at(-1)!.push(p); });
  const label = views.find(([id]) => id === selection.view)![1];
  return <section className="mk-price-context mk-panel">
    <button className="mk-context-disclosure" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
      <span><strong>가격 자세히 보기</strong><small>기간 변화 · 고점 대비 · 월별 변화</small></span><span aria-hidden="true">{open ? "−" : "+"}</span>
    </button>
    {open && <div id={bodyId}>
      <div className="mk-section-head"><h2>{label}</h2><button className="mk-button" onClick={() => setHelp(true)}>뜻 보기</button></div>
      <div className="mk-context-views" role="group" aria-label="가격 분석 선택">{views.map(([view, title]) => <button className="mk-button" key={view} aria-label={title} aria-pressed={selection.view === view} onClick={() => update({view})}>{title.split(" ").map(word => <span key={word}>{word}</span>)}</button>)}</div>
      <label className="mk-context-period">분석 기간<select value={selection.years} onChange={event => {update({years: Number(event.target.value), day: ""}); setMonthYear(null);}}>{[[1,"1년"],[3,"3년"],[5,"5년"],[10,"10년"],[0,"전체"]].map(([v,t]) => <option key={v} value={v}>{t}</option>)}</select></label>
      {error && <p role="status">{error} {data ? "마지막으로 불러온 자료입니다." : ""} <button className="mk-button" onClick={() => setRetry(v => v+1)}>다시 시도</button></p>}
      {!data && !error && <div className="mk-context-loading" role="status">가격을 비교하는 중…</div>}
      {data && !points.length && <p className="mk-empty">{data.status === "incompatible_data" ? "가격 기준을 확인 중입니다. 확인된 자료만 비교합니다." : "비교할 과거 가격이 아직 없습니다."}</p>}
      {data && selected && <>
        <p className="mk-context-caption">{data.summary.start} ~ {data.summary.end} · 종가 기준 · 배당 제외</p>
        {selection.view === "monthly" ? <>
          <label className="mk-context-period">연도<select value={activeYear} onChange={event => {setMonthYear(event.target.value); const month = data.months.filter(m => m.month.startsWith(event.target.value) && m.end).at(-1); if (month?.end) update({day: month.end});}}>{monthlyYears.map(year => <option key={year}>{year}</option>)}</select></label>
          <div className="mk-month-grid" aria-label={`${activeYear}년 월별 가격 변화`}>{months.map(m => <button key={m.month} className={`mk-button ${tone(m.return_pct)}`} aria-pressed={activeMonth?.month === m.month} disabled={!m.end}
            onClick={() => update({day: m.end!})}><span>{Number(m.month.slice(5))}월</span><strong>{percent(m.return_pct)}</strong></button>)}</div>
          {activeMonth && <div className="mk-context-month-reading" aria-live="polite"><strong>{activeMonth.month} · {percent(activeMonth.return_pct)}</strong><p>{activeMonth.return_pct == null ? "직전 달 자료가 없어 계산하지 않습니다." : `${activeMonth.start} ${format(activeMonth.start_close)}원 → ${activeMonth.end} ${format(activeMonth.end_close)}원`}</p></div>}
          <p className="mk-context-caption">각 달의 마지막 관측값을 비교합니다. {data.summary.end.slice(0,7)}은 {data.summary.end}까지입니다.</p>
        </> : <>
          <div className="mk-context-scale"><span>{percent(high)}</span><span>{percent(low)}</span></div>
          <svg className="mk-context-plot" viewBox="0 0 800 222" preserveAspectRatio="none" role="img" aria-labelledby={plotId} onClick={event => {
            const bounds = event.currentTarget.getBoundingClientRect();
            const target = firstTime + Math.max(0, Math.min(1, ((event.clientX - bounds.left) / bounds.width * 800 - 16) / 768)) * timeSpan;
            const nearest = points.reduce((best, p) => Math.abs(Date.parse(p.date)-target) < Math.abs(Date.parse(best.date)-target) ? p : best, points[0]);
            update({day: nearest.date});
          }}><title id={plotId}>{name} {label}. 아래 날짜 버튼으로 값을 확인할 수 있습니다.</title>
            <line x1="16" x2="784" y1={y(0)} y2={y(0)} stroke="var(--mk-line)" />
            {segments.filter(s => s.length > 1).map((segment, i) => <polyline key={i} points={segment.map(p => `${x(p)},${y(value(p))}`).join(" ")} fill="none" stroke="var(--mk-accent)" strokeWidth="2.5" vectorEffect="non-scaling-stroke"/>)}
            <line x1={x(selected)} x2={x(selected)} y1="8" y2="212" stroke="var(--mk-muted)" strokeDasharray="4 5"/>
            <circle cx={x(selected)} cy={y(value(selected))} r="5" fill="var(--mk-accent)"/>
          </svg>
          <div className="mk-trend-dates"><span>{data.summary.start}</span><span>{data.summary.end}</span></div>
        </>}
        <div className="mk-context-reading" aria-live="polite"><strong>{selected.date}</strong><dl>
          <div><dt>종가</dt><dd>{format(selected.close)}<small>원</small></dd></div>
          <div><dt>시작 대비</dt><dd className={tone(selected.return_pct)}>{percent(selected.return_pct)}</dd></div>
          <div><dt>고점 대비</dt><dd className={tone(selected.drawdown_pct)}>{percent(selected.drawdown_pct)}</dd></div>
        </dl></div>
        <label className="mk-context-date">날짜 선택<input type="date" min={data.summary.start} max={data.summary.end} value={selected.date} onChange={event => isDay(event.target.value) && update({day: event.target.value})}/></label>
        <div className="mk-context-nav"><button className="mk-button" disabled={index === 0} onClick={() => update({day: points[index-1].date})}>이전 날짜</button><button className="mk-button" disabled={index === points.length-1} onClick={() => update({day: points[index+1].date})}>다음 날짜</button><button className="mk-button" onClick={() => update({day: ""})}>최근</button></div>
        {selection.view === "drawdown" && <p className="mk-context-caption">이 기간의 가장 큰 하락은 {percent(data.summary.max_drawdown_pct)}입니다. {data.summary.peak_date} 고점 → {data.summary.trough_date}</p>}
        <details><summary>숫자 표 · 저장 · 계산 방법</summary>
          <div className="mk-context-tools"><button className="mk-button" onClick={share}>이 차트 링크 복사</button><button className="mk-button" onClick={exportCsv}>숫자 CSV 저장</button></div><p role="status">{notice}</p>
          <p>고점 대비는 선택 기간의 이전 최고 종가와 비교합니다. 휴장일은 채워 넣지 않으며, 과거 가격의 마지막 관측값을 사용합니다.</p>
          <p>가격 기준: {data.basis.adjustment || "조정 방식 미확인"}{data.basis.venue ? ` · ${data.basis.venue}` : ""}. 조정 방식이 확인되지 않은 구간은 주식분할의 영향을 포함할 수 있습니다. 배당·세금·수수료는 포함하지 않습니다.</p>
          {data.source_trace?.source_url && /^https?:\/\//.test(data.source_trace.source_url) && <a href={data.source_trace.source_url} target="_blank" rel="noreferrer">가격 자료 원문</a>}
          <div className="mk-table-scroll" tabIndex={0} role="region" aria-label="월별 가격 변화 숫자 표"><table><thead><tr><th>월</th><th>시작일</th><th>마지막 관측일</th><th>변화</th></tr></thead><tbody>{data.months.map(m => <tr key={m.month}><th scope="row">{m.month}</th><td>{m.start || "—"}</td><td>{m.end || "—"}</td><td className={tone(m.return_pct)}>{percent(m.return_pct)}</td></tr>)}</tbody></table></div>
        </details>
      </>}
    </div>}
    {help && <Dialog title="가격 변화 읽는 방법" onClose={() => setHelp(false)}><div className="mk-context-guide">
      <article><h3>얼마나 올랐거나 내렸나요?</h3><p>첫 관측일의 종가와 선택한 날짜의 종가를 비교합니다. 10,000원이 11,000원이 되면 +10%입니다.</p><button className="mk-button" onClick={() => chooseView("return")}>가격 변화 차트 보기</button></article>
      <article><h3>고점에서 얼마나 내려왔나요?</h3><p>선택한 기간 안에서, 그날까지의 최고 종가보다 얼마나 낮은지 보여줍니다. 10,000원에서 8,000원이면 −20%입니다.</p><button className="mk-button" onClick={() => chooseView("drawdown")}>고점 대비 차트 보기</button></article>
      <article><h3>어느 달에 많이 움직였나요?</h3><p>직전 달 마지막 관측 종가와 이번 달 마지막 관측 종가를 비교합니다. 빈 달은 0%로 채우지 않습니다.</p><button className="mk-button" onClick={() => chooseView("monthly")}>월별 변화 보기</button></article>
    </div></Dialog>}
  </section>;
});
