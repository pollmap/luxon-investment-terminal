"use client";
import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
const FinancialChart=dynamic(()=>import("./financial-chart"),{loading:()=> <div className="mk-financial-canvas" style={{height:"var(--mk-chart-height, 420px)"}} aria-busy="true"/>});
import { request, format, type Price } from "./types";
import type { DetailSelection } from "./detail-preferences";
import {chartCutoff} from "./chart-period";
import {historyChartPath,type FirstChartRequest} from './first-chart-request';

const validCandle = (p:Price) => [p.open,p.high,p.low,p.close].every(v=>Number.isFinite(v)&&v>0)&&p.low<=Math.min(p.open,p.close)&&p.high>=Math.max(p.open,p.close);
const EMPTY:Price[]=[];
const PERIODS = [[21,"1개월"],[63,"3개월"],[252,"1년"],[756,"3년"],[1260,"5년"],[2600,"10년"],[100000,"전체"]] as const;
const SHAPES = [["line","선"],["area","영역"],["candle","캔들"],["bar","OHLC"]] as const;
type WindowRow=Price & {ma20:number|null};

export default function PriceChart({
  rows = EMPTY,
  code,
  small = false,
  selection,
  onSelectionChange,
  intraday = false,
  chartKind,
  onIntraday,
  firstRequest,
}: {
  rows?: Price[];
  code?: string;
  small?: boolean;
  selection?: DetailSelection;
  onSelectionChange?: (patch: Partial<DetailSelection>) => void;
  intraday?: boolean;
  chartKind?: string;
  onIntraday?: () => void;
  firstRequest?:FirstChartRequest;
}) {
  const [localRange, setLocalRange] = useState(63);
  const [localKind, setLocalKind] = useState("candle");
  const range = intraday ? rows.length : selection?.range ?? localRange, kind = chartKind ?? selection?.kind ?? localKind;
  const setRange = (range: number) => onSelectionChange ? onSelectionChange({range}) : setLocalRange(range);
  const setKind = (kind: string) => onSelectionChange ? onSelectionChange({kind}) : setLocalKind(kind);
  const [cursorDate, setCursorDate] = useState<string | null>(null);
  const [tablePage, setTablePage] = useState(0);
  const [ma, setMa] = useState(false);
  const [localBar, setLocalBar] = useState("day");
  const bar = intraday ? "day" : selection?.bar ?? localBar;
  const setBar = (bar: string) => onSelectionChange ? onSelectionChange({bar}) : setLocalBar(bar);
  const selectionKey=`${code}:${range}:${bar}`;
  const [windowData,setWindowData]=useState<{key:string;rows:WindowRow[]}|null>(null);
  const [loadError,setLoadError]=useState("");
  const [retry,setRetry]=useState(0);
  useEffect(()=>{
    if(!code)return;
    const abort=new AbortController();let busy=false,initial=true;
    const load=async()=>{if(document.hidden||busy)return;busy=true;try{
      const path=historyChartPath(code,range,bar);
      const prefetched=initial?firstRequest:undefined;initial=false;
      const result=await (prefetched?prefetched.read<{rows:WindowRow[]}>(path,abort.signal):request<{rows:WindowRow[]}>(path,{signal:abort.signal}));
      if(!abort.signal.aborted){setWindowData({key:selectionKey,rows:result.rows});setLoadError("");}
    }catch{if(!abort.signal.aborted)setLoadError("차트를 불러오지 못했어요.");}finally{busy=false;}};
    void load();const timer=setInterval(load,60000);document.addEventListener('visibilitychange',load);
    return()=>{abort.abort();clearInterval(timer);document.removeEventListener('visibilitychange',load);};
  },[code,range,bar,selectionKey,retry,firstRequest]);
  const data = useMemo(() => {
    if(code)return windowData?.key===selectionKey?windowData.rows.map(p=>({...p,valid:validCandle(p)})):[];
    const ordered=rows.filter(p=>Number.isFinite(p.close)&&p.close>0).sort((a,b)=>a.date.localeCompare(b.date));
    const grouped = new Map<string, Price & {valid:boolean}>();
    for (const p of ordered) {
      const day = new Date(p.date + "T00:00:00Z");
      if (bar === "week")
        day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
      const key = bar === "day" ? p.date : bar === "month" ? p.date.slice(0, 7) : day.toISOString().slice(0, 10);
      const previous = grouped.get(key);
      grouped.set(
        key,
        previous
          ? {
              ...previous,
              date: p.date,
              high: Math.max(previous.high, p.high),
              low: Math.min(previous.low, p.low),
              close: p.close,
              volume: Number.isFinite(previous.volume)&&Number.isFinite(p.volume) ? previous.volume + p.volume : NaN,
              valid: previous.valid && validCandle(p),
            }
          : { ...p, valid:validCandle(p) },
      );
    }
    const full=[...grouped.values()];
    const cutoff=intraday?'':chartCutoff(ordered.at(-1)?.date||'',range);
    return full.map((p,i)=>({...p,ma20:i>=19?full.slice(i-19,i+1).reduce((s,q)=>s+q.close,0)/20:null})).filter(p=>p.date>=cutoff);
  }, [rows, range, bar, intraday,code,windowData,selectionKey]);
  const chartRows = useMemo(()=>data.map(p=>({...p,time:p.date})),[data]);
  const foundCursor=cursorDate===null?-1:data.findIndex(row=>row.date===cursorDate);
  const cursor=foundCursor<0?null:foundCursor;
  const setCursor=(index:number|null)=>setCursorDate(index===null?null:data[index]?.date??null);
  const labelDate = (value:string) => intraday ? value.slice(11,16) : value;
  const active = data[Math.min(cursor ?? data.length - 1, data.length - 1)];
  const chooseRange = (value:number) => {setRange(value);setCursor(null);setTablePage(0);};
  const chooseBar = (value:string) => {setBar(value);setCursor(null);setTablePage(0);};
  return (
    <div className="mk-chart">
      {!small && !intraday && (
        <><div className="mk-price-controls-mobile" aria-label="가격 차트 설정">
          <select aria-label="차트 기간 선택" value={String(range)} onChange={event=>event.target.value==='intraday'?onIntraday?.():chooseRange(Number(event.target.value))}>
            {onIntraday && <option value="intraday">하루</option>}
            {PERIODS.map(([value,label])=><option key={value} value={value}>{label}</option>)}
          </select>
          <select aria-label="차트 봉 선택" value={bar} onChange={event=>chooseBar(event.target.value)}><option value="day">일봉</option><option value="week">주봉</option><option value="month">월봉</option></select>
          <select aria-label="가격 차트 모양" value={kind} onChange={event=>setKind(event.target.value)}>{SHAPES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
        </div><div className="mk-chart-controls mk-price-controls-desktop">
          <div className="mk-segments" aria-label="차트 기간">
            {onIntraday && <button onClick={onIntraday}>하루</button>}
            {PERIODS.map(([n, label]) => (
              <button
                key={n}
                aria-pressed={range === n}
                onClick={() => chooseRange(n)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="mk-row">
            <select
              aria-label="차트 봉 선택"
              value={bar}
              onChange={(e) => chooseBar(e.target.value)}
            >
              <option value="day">일봉</option>
              <option value="week">주봉</option>
              <option value="month">월봉</option>
            </select>
            <div className="mk-segment" aria-label="가격 차트 모양">{SHAPES.map(([value,label])=><button key={value} aria-pressed={kind===value} onClick={()=>setKind(value)}>{label}</button>)}</div>
            <label className="mk-check">
              <input
                type="checkbox"
                checked={ma}
                onChange={(e) => setMa(e.target.checked)}
              />
              20{bar === "day" ? "일" : "봉"}선
            </label>
          </div>
        </div></>
      )}
      {!active ? <div className="mk-chart-empty" role="status">{loadError||(!windowData||windowData.key!==selectionKey?"차트를 불러오는 중이에요.":"이 기간의 가격 자료가 없어요.")}{loadError&&<button className="mk-button" onClick={()=>{firstRequest?.reset();setLoadError("");setRetry(v=>v+1);}}>다시 시도</button>}</div> : <>
      <FinancialChart rows={chartRows} kind={kind} rangeKey={`${code??'local'}:${intraday?'intraday':range}:${bar}`} movingAverage={ma} onSelect={setCursor} label={`${data[0].date}부터 ${data[data.length-1].date}까지 주가와 거래량`}/>
      <div className="mk-chart-caption">
        <strong>{intraday?active.date.slice(0,16).replace("T"," "):active.date}</strong>
        <span>
          종가 <b>{format(active.close)}원</b>
        </span>
        {(kind==='candle'||kind==='bar')&&<><span>시가 {format(active.valid?active.open:null)}</span><span>고가 {format(active.valid?active.high:null)}</span><span>저가 {format(active.valid?active.low:null)}</span></>}
        <span>거래량 {format(Number.isFinite(active.volume)?active.volume:null)}주</span>
      </div>
      {!small && !intraday && <label className="mk-price-extra-mobile mk-check"><input type="checkbox" checked={ma} onChange={event=>setMa(event.target.checked)}/>20{bar==='day'?'일':'봉'}선</label>}

      {!small && <div className="mk-segment" aria-label="차트 날짜 이동"><button disabled={(cursor??data.length-1)===0} onClick={() => setCursor(Math.max(0, (cursor ?? data.length-1)-1))}>{intraday?"이전 봉":"이전 날짜"}</button><button disabled={(cursor??data.length-1)>=data.length-1} onClick={() => setCursor(Math.min(data.length-1, (cursor ?? data.length-1)+1))}>{intraday?"다음 봉":"다음 날짜"}</button><button onClick={() => setCursor(null)}>최근</button></div>}
      {!small && (
        <details className="mk-chart-data">
          <summary>차트 수치 표로 보기 · 전체 {data.length.toLocaleString()}개 봉</summary>
          <div className="mk-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>날짜</th>
                  <th>시가</th>
                  <th>고가</th>
                  <th>저가</th>
                  <th>종가</th>
                  <th>거래량</th>
                </tr>
              </thead>
              <tbody>
                {data
                  .slice()
                  .reverse().slice(Math.min(tablePage,Math.max(0,Math.ceil(data.length/60)-1))*60,(Math.min(tablePage,Math.max(0,Math.ceil(data.length/60)-1))+1)*60)
                  .map((p) => (
                    <tr key={p.date}>
                      <td>{labelDate(p.date)}</td>
                      {[p.valid?p.open:null, p.valid?p.high:null, p.valid?p.low:null, p.close, Number.isFinite(p.volume)?p.volume:null].map(
                        (v, i) => (
                          <td key={i}>{format(v)}</td>
                        ),
                      )}
                    </tr>
                  ))}
              </tbody>
            </table>
          </div><div className="mk-segment"><button disabled={tablePage===0} onClick={()=>setTablePage(n=>Math.max(0,n-1))}>이전 60개</button><button disabled={(tablePage+1)*60>=data.length} onClick={()=>setTablePage(n=>n+1)}>다음 60개</button></div>
        </details>
      )}
      </>}
    </div>
  );
}
