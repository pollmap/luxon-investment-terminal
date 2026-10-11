"use client";

import { useEffect, useState } from "react";
import { format, request } from "./types";

type Event = {
  id: string; date?: string; date_kind?: string; kind: string;
  record_date?: string; payment_date?: string; effective_date?: string;
  amount?: string | null; title?: string; payment_status?: string; ex_date?: string; filing_id?: string; superseded_by?: string;
};
type Events = { rows: Event[]; status: string };

function EventList({ code }: { code: string }) {
  const [data, setData] = useState<Events | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [count, setCount] = useState(8);
  useEffect(() => {
    const abort = new AbortController();
    setData(null); setError(false); setCount(8);
    request<Events>(`/securities/${code}/events`, { signal: abort.signal })
      .then(value => { if (!abort.signal.aborted) setData(value); })
      .catch(() => { if (!abort.signal.aborted) setError(true); });
    return () => abort.abort();
  }, [code, retry]);
  if (error) return <p role="alert">일정을 불러오지 못했습니다. <button className="mk-button" onClick={() => setRetry(n => n + 1)}>다시 시도</button></p>;
  if (!data) return <p role="status">일정 확인 중…</p>;
  const rows = data.rows.filter(row => !row.superseded_by).sort((a, b) => (b.date || b.effective_date || "").localeCompare(a.date || a.effective_date || ""));
  if (!rows.length) return <p className="mk-caption">{data.status === "no_observations" ? "조회 기간에 확인된 일정이 없습니다." : "현재 확인할 수 있는 일정이 없습니다."}</p>;
  return <>
    <ol className="mk-company-events">
      {rows.slice(0, count).map(row => {
        const payment = row.date_kind === "payment" || (!!row.payment_date && row.date === row.payment_date);
        const value = row.amount == null ? null : Number(row.amount);
        return <li key={row.id}>
          <time>{row.date || row.effective_date || row.record_date || "날짜 확인 중"}</time>
          <div><strong>{row.kind === "dividend" ? payment ? row.payment_status==='confirmed'?'배당금 지급':'배당금 지급 예정일' : "배당 기준일" : row.title || "기업 행사"}</strong>
            {row.kind === "dividend" && <p>{value != null && Number.isFinite(value) ? `주당 ${format(value, "raw")}원` : "배당금 확인 중"}{payment && row.record_date ? ` · 기준일 ${row.record_date}` : ""}</p>}
            {row.ex_date&&<p>배당락일 {row.ex_date}</p>}
            {row.filing_id&&/^\d{14}$/.test(row.filing_id)&&<a href={`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${row.filing_id}`} target="_blank" rel="noreferrer">{row.title?.includes('정정')?'정정 공시':'공시 원문'}</a>}
          </div>
        </li>;
      })}
    </ol>
    {rows.length > count && <button className="mk-button" onClick={() => setCount(n => n + 20)}>이전 일정 더 보기</button>}
  </>;
}

export default function EventsPanel({ code }: { code: string }) {
  const [open, setOpen] = useState(false);
  return <details className="mk-panel mk-company-events-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>배당·기업 일정</summary>
    {open && <EventList key={code} code={code} />}
  </details>;
}
