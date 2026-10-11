"use client";
import PriceChart from "./price-chart";
import dynamic from "next/dynamic";
const FinancialChart=dynamic(()=>import("./financial-chart"),{loading:()=> <div className="mk-financial-canvas" style={{height:360}} aria-busy="true"/>});
import type {Price} from "./types";
import {intradayWindow} from './chart-period';
import {intradayLinePoints,type LineInterval} from './intraday-line';
import {useVisibleQuery} from './use-visible-query';
import {intradayChartPath,type FirstChartRequest} from './first-chart-request';

import { useEffect, useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { format, request, tone, type Overview } from "./types";

type Point = { time: string; value: number };
const EMPTY_POINTS: Point[] = [];
export type Series = {
  points: Point[];
  baseline?: number;
  return_pct?: number;
  status: string;
  session_date?: string;
  session_mismatch?: boolean;
  data_as_of?: string;
};

const ranges = [
  ["day", "하루"],
  ["week", "1주"],
  ["month", "1개월"],
  ["year", "1년"],
];
const names: Record<string, string> = {
  KOSPI: "코스피",
  KOSDAQ: "코스닥",
  KPI200: "코스피 200",
};

function dateLabel(time: string) {
  return time.length > 8
    ? `${time.slice(8, 10)}:${time.slice(10, 12)}`
    : `${time.slice(0, 4)}.${time.slice(4, 6)}.${time.slice(6, 8)}`;
}

export function LinePlot({
  data,
  mini = false,
  label = "가격 추이",
  unit = "포인트",
  kind = "line",
  intervalLabel,
  rangeKey,
}: {
  data: Series;
  mini?: boolean;
  label?: string;
  unit?: string;
  kind?: string;
  intervalLabel?:string;
  rangeKey?:string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const validPoints = Array.isArray(data.points) && data.points.every(point =>
    point && typeof point.time === "string" && typeof point.value === "number" && Number.isFinite(point.value));
  const points = validPoints ? data.points : EMPTY_POINTS;
  const selectPoint = (index:number|null) => setHover(index===null ? null : points[Math.max(0,Math.min(points.length-1,index))]?.time ?? null);
  const chartRows=useMemo(()=>points.map(p=>({time:p.time,close:p.value})),[points]);

  if (points.length < 2) {
    return (
      <div className={mini ? "mk-mini-empty" : "mk-chart-empty"}>
        {mini
          ? ""
          : data.status === "loading" || data.status === "collecting" || data.status === "pending"
            ? "차트를 불러오는 중…"
            : !validPoints || data.status === "unavailable" || data.status === "error"
              ? "차트를 불러오지 못했어요."
              : "이 기간의 차트 자료가 없어요."}
      </div>
    );
  }

  const width = mini ? 200 : 900;
  const height = mini ? 58 : 240;
  const padding = mini ? 3 : 25;
  const low = Math.min(
    ...points.map((point) => point.value),
    data.baseline ?? Infinity,
  );
  const high = Math.max(
    ...points.map((point) => point.value),
    data.baseline ?? -Infinity,
  );
  const spread = high - low || high * 0.01 || 1;
  const x = (index: number) =>
    padding + (index / (points.length - 1)) * (width - padding * 2);
  const y = (value: number) =>
    height - padding - ((value - low) / spread) * (height - padding * 2);
  const path = points
    .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`)
    .join(" ");
  const positive =
    (points.at(-1)?.value ?? 0) >= (data.baseline ?? points[0].value);
  const color = positive ? "var(--mk-up)" : "var(--mk-down)";
  const selected = hover === null ? -1 : points.findIndex(point=>point.time>=hover);
  const index = selected<0 ? points.length-1 : selected;

  return (
    <div className={mini ? "mk-spark" : "mk-line-chart"}>
      {!mini && (
        <div className="mk-chart-readout">
          <time>
            {points[index].time.length > 8
              ? `${points[index].time.slice(4, 6)}.${points[index].time.slice(6, 8)} `
              : ""}
            {dateLabel(points[index].time)}
          </time>
          <strong>{format(points[index].value, "index")}</strong>
          <span className="mk-caption">{unit}</span>
        </div>
      )}
      {!mini ? <FinancialChart rows={chartRows} kind={kind} rangeKey={rangeKey} onSelect={selectPoint} label={label}/> : (      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${label}, ${points.length}개 구간`}
        onPointerMove={
          mini
            ? undefined
            : (event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                selectPoint(
                  Math.max(
                    0,
                    Math.min(
                      points.length - 1,
                      Math.round(
                        (((event.clientX - rect.left) / rect.width) * width -
                          padding) /
                          (width - 2 * padding) *
                          (points.length - 1),
                      ),
                    ),
                  ),
                );
              }
        }
        onPointerDown={mini ? undefined : event => { const box=event.currentTarget.getBoundingClientRect(); selectPoint(Math.max(0,Math.min(points.length-1,Math.round((event.clientX-box.left)/box.width*(points.length-1))))); }}
        style={{touchAction:"pan-y"}}
      >
        {!mini &&
          [0, 0.5, 1].map((portion) => (
            <g key={portion}>
              <line
                x1={padding}
                x2={width - padding}
                y1={y(low + spread * portion)}
                y2={y(low + spread * portion)}
                stroke="var(--mk-line)"
                strokeDasharray="3 4"
              />
              <text
                x={width - padding}
                y={y(low + spread * portion) - 5}
                textAnchor="end"
                fill="var(--mk-muted)"
                fontSize="12"
              >
                {format(low + spread * portion, "index")}
              </text>
            </g>
          ))}
        <path
          d={`${path} L${x(points.length - 1)},${height - padding} L${padding},${height - padding} Z`}
          fill={color}
          fillOpacity={0.08}
        />
        {data.baseline !== undefined && (
          <line
            x1={padding}
            x2={width - padding}
            y1={y(data.baseline)}
            y2={y(data.baseline)}
            stroke="var(--mk-muted)"
            strokeDasharray="4 5"
            opacity=".5"
          />
        )}
        <path
          d={path}
          fill="none"
          stroke={color}
          strokeWidth={mini ? 2 : 2.3}
          vectorEffect="non-scaling-stroke"
        />
        {!mini && hover !== null && (
          <g>
            <line
              x1={x(index)}
              x2={x(index)}
              y1={padding}
              y2={height - padding}
              stroke="var(--mk-muted)"
            />
            <circle cx={x(index)} cy={y(points[index].value)} r="4" fill={color} />
          </g>
        )}
      </svg>)}

      {!mini && (
        <>
          <div className="mk-chart-axis">
            <span>{dateLabel(points[0].time)}</span>
            <span>{dateLabel(points.at(-1)!.time)}</span>
          </div>
          <div className="mk-segment" aria-label="차트 시점 이동"><button disabled={index===0} onClick={() => selectPoint(index-1)}>이전 시점</button><button disabled={index===points.length-1} onClick={() => selectPoint(index+1)}>다음 시점</button><button onClick={() => selectPoint(null)}>최근 시점</button></div>
          <details className="mk-chart-data">
            <summary>차트 데이터 보기{intervalLabel ? ` · ${intervalLabel}` : ""}</summary>
            <div className="mk-table-scroll">
              <table className="mk-data-table">
                <thead>
                  <tr>
                    <th>시점</th>
                    <th>{unit === "원" ? "주가 (원)" : "지수"}</th>
                  </tr>
                </thead>
                <tbody>
                  {points.map((point) => (
                    <tr key={point.time}>
                      <th>{point.time}</th>
                      <td>{format(point.value, "index")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

export default function MarketPulse({
  overview,
}: {
  overview: Overview | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [code, setCode] = useState("KOSPI");
  const [period, setPeriod] = useState("day");
  const kospi=useVisibleQuery<Series>("/indices/KOSPI/chart?period=day",120000);
  const kosdaq=useVisibleQuery<Series>("/indices/KOSDAQ/chart?period=day",120000);
  const kpi200=useVisibleQuery<Series>("/indices/KPI200/chart?period=day",120000);
  const longRange=useVisibleQuery<Series>(expanded&&period!=="day"?`/indices/${code}/chart?period=${period}`:null,120000);
  const daily={KOSPI:kospi,KOSDAQ:kosdaq,KPI200:kpi200};
  const value=(result:typeof kospi):Series=>result.data??{points:[],status:result.error?"unavailable":"loading"};
  const series:Record<string,Series>=Object.fromEntries(Object.entries(daily).map(([key,result])=>[key,value(result)]));
  // The expanded day chart and its sparkline read one response.
  const data=period==="day"?series[code]:value(longRange);

  return (
    <section className="mk-pulse" aria-label="시장 지수 차트">
      <div className="mk-index-choices">
        {Object.entries(names).map(([indexCode, name]) => {
          const index = overview?.indices?.find((item) => item.code === indexCode);
          return (
            <button
              key={indexCode}
              onClick={() => {
                setCode(indexCode);
                setExpanded(true);
              }}
              aria-pressed={expanded && code === indexCode}
              aria-label={`${name} 차트 보기`}
            >
              <span>{name}</span>
              <strong>{format(index?.price, "index")}</strong>
              <b className={tone(index?.change)}>{format(index?.change, "change")}</b>
              <LinePlot
                mini
                data={series[indexCode] || { points: [], status: "loading" }}
                label={name}
              />
            </button>
          );
        })}
      </div>
      <button
        className={`mk-index-expand${expanded ? " is-expanded" : ""}`}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? "상세 차트 접기 ↑" : "지수 상세 · 하루 / 1주 / 1개월 / 1년 ↗"}
      </button>
      {expanded && (
        <div className="mk-pulse-detail">
          <div className="mk-section-head">
            <h2>{names[code]}의 흐름</h2>
            <div className="mk-segment" role="group" aria-label="지수 차트 기간">
              {ranges.map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={period === value}
                  onClick={() => setPeriod(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <LinePlot data={data} label={names[code]} rangeKey={`${code}:${period}`} />
          <div className="mk-chart-context">
            <span className={tone(data.return_pct)}>
              {(data.return_pct ?? 0) >= 0 ? (
                <ArrowUpRight size={16} />
              ) : (
                <ArrowDownRight size={16} />
              )}
              {format(data.return_pct, "change")}
            </span>
            <span>
              {period === "day"
                ? "전일 종가 대비"
                : period === "week"
                  ? "주간 기준"
                  : "선택 기간 기준"}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

export function StockIntraday({
  code,
  name,
  period = "day",
  onPeriodChange,
  kind = "line",
  onKindChange,
  quoteDate,
  onHistory,
  lineInterval="1min",
  onLineIntervalChange,
  firstRequest,
}: {
  code: string;
  name: string;
  period?: string;
  onPeriodChange?: (period: string) => void;
  kind?: string;
  onKindChange?: (kind:string) => void;
  quoteDate?: string;
  onHistory?: () => void;
  lineInterval?:LineInterval;
  onLineIntervalChange?:(interval:LineInterval)=>void;
  firstRequest?:FirstChartRequest;
}) {
  const [data, setData] = useState<Series>({ points: [], status: "loading" });
  const [candles,setCandles]=useState<Price[]>([]);
  const lineData=useMemo(()=>({...data,points:intradayLinePoints(data.points,lineInterval)}),[data,lineInterval]);
  const candleMode=kind==="candle"||kind==="bar";

  useEffect(() => {
    let active = true,busy=false,initial=true;const abort=new AbortController();
    setData({ points: [], status: "loading" });
    setCandles([]);
    const load = async () => {
      if(document.hidden||busy) return;busy=true;
      try{
        const path=intradayChartPath(code,period,candleMode?'candle':'line');
        const prefetched=initial?firstRequest:undefined;initial=false;
        const read=<T,>()=>prefetched?prefetched.read<T>(path,abort.signal):request<T>(path,{signal:abort.signal});
        if(candleMode){
          const value=await read<{rows:(Price&{time?:string;ohlc_valid?:boolean})[];status:string}>();
          if(active){const visible=intradayWindow(value.rows,period).filter(r=>r.ohlc_valid!==false);setCandles(visible.map(r=>({...r,date:r.time!})));setData({points:[],status:value.status,session_date:visible.at(-1)?.time?.slice(0,10)});}
        }else{
          const value=await read<Series>();
          if(active)setData(value);
        }
      }catch{if(active)setData(previous=>({...previous,status:"unavailable"}));}finally{busy=false;}
    };
    load();
    const timer = setInterval(load, candleMode?30000:60000);
    document.addEventListener("visibilitychange", load);
    return () => {
      active = false;
      abort.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
    };
  }, [code, period, candleMode, firstRequest]);

  return (
    <>
      <div className="mk-intraday-toolbar">
      <div className="mk-segment" aria-label="장중 차트 기간">
        {[
          ["day", "하루"],
          ["week", "1주"],
        ].map(([value, label]) => (
          <button
            key={value}
            aria-pressed={period === value}
            onClick={() => onPeriodChange?.(value)}
          >
            {label}
          </button>
        ))}
        {onHistory&&<button onClick={onHistory}>장기 차트</button>}
      </div>
      <div className="mk-segment" aria-label="가격 차트 모양">{[["line","선"],["area","영역"],["candle","캔들 · 5분"],["bar","OHLC · 5분"]].map(([value,label])=><button key={value} aria-pressed={kind===value} onClick={()=>onKindChange?.(value)}>{label}</button>)}</div>
      {(kind==="line"||kind==="area")&&<select aria-label="선 차트 간격" className="mk-line-interval" value={lineInterval} onChange={event=>onLineIntervalChange?.(event.target.value as LineInterval)}><option value="1min">1분 원본</option><option value="5min">5분 종가</option></select>}
      {data.session_date&&<span className="mk-chart-session" role="status">{data.session_date.replaceAll("-", ".")} · 통합{(data.status==="stale" || (period==="day"&&quoteDate&&data.session_date<quoteDate.slice(0,10)))?" · 이전 거래일":""}</span>}
      </div>
      {(kind==="candle"||kind==="bar") ? candles.length ? <PriceChart rows={candles} intraday chartKind={kind}/> : <div className="mk-chart-empty" role="status">{data.status==="loading"?"5분봉을 불러오는 중…":data.status==="unavailable"||data.status==="error"?"5분봉을 불러오지 못했어요.":"이 기간의 5분봉 자료가 없어요."}{data.status!=="loading"&&<><button className="mk-button" onClick={()=>onKindChange?.("line")}>선 차트 보기</button>{onHistory&&<button className="mk-button" onClick={onHistory}>일봉 차트 보기</button>}</>}</div> : <><LinePlot data={lineData} label={name} unit="원" kind={kind} rangeKey={`${code}:${period}:${lineInterval}`} intervalLabel={lineInterval==="5min"?"5분 구간 마지막 종가":"1분 원본"}/>{data.points.length<2&&!["loading","collecting","pending"].includes(data.status)&&onHistory&&<button className="mk-button" onClick={onHistory}>일봉 차트 보기</button>}</>}
    </>
  );
}
