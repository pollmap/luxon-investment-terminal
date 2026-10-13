"use client";
import { useEffect, useId, useMemo, useState,type CSSProperties } from "react";
import {PaneSeparator,usePanelWidth} from './workspace-split';
import {subscribeQuotes} from "./quote-subscription";
import { detailLoader } from "./detail-loader";
import { useDetailSelection } from "./detail-preferences";
import {createFirstChartRequest,detailChartPath} from './first-chart-request';
import dynamic from "next/dynamic";
import ActivityPanel from "./activity-panel";
import {StockWarnings} from "./market-data";
const ShortSellingPanel = dynamic(() => import("./short-selling-panel"), {loading: () => <p role="status">공매도 화면을 여는 중…</p>});
const DepthPanel = dynamic(() => import("./depth-panel"), {loading: () => <p role="status">호가·체결 화면을 여는 중…</p>});
const PriceContextPanel = dynamic(() => import("./price-context-panel"));
import { ArrowLeft, ExternalLink, Info, Star } from "lucide-react";
import PriceChart from "./price-chart";
import {StockIntraday} from "./market-pulse";
import ValuationHistory from "./valuation-chart";
import EarningsVisual from "./earnings-overview";
import CompanyLogo from "./company-logo";
import Dialog from "./dialog";
import DartCompanyFacts from "./dart-company-facts";
import EventsPanel from "./events-panel";
const TtmPanel = dynamic(() => import("./company-research").then(m=>m.TtmPanel),{loading:()=> <p role="status">최근 12개월 실적을 불러오는 중이에요.</p>});
const EstimatesPanel = dynamic(() => import("./company-research").then(m=>m.EstimatesPanel),{loading:()=> <p role="status">예상실적을 불러오는 중이에요.</p>});
const PeersPanel = dynamic(() => import("./company-research").then(m=>m.PeersPanel),{loading:()=> <p role="status">비교 기업을 불러오는 중이에요.</p>});
const DisclosuresPanel = dynamic(() => import("./disclosures-panel"),{loading:()=> <p role="status">공시·뉴스를 불러오는 중이에요.</p>});
import {PerformanceStrip,OfficialStatements} from "./company-insights";
import {
  format,
  tone,
  type Detail,
  type MetricId,
  type Stock,
} from "./types";

function financialUnit(name: string, unit?: string) {
  if (name === "ROE" || name === "ROA" || name.includes("율") || name.includes("률"))
    return "%";
  if (name === "PER" || name === "PBR") return "배";
  return unit || "—";
}

export default function StockDetail({
  code,
  favorite,
  onFavorite,
  onBack,
  onMetric,
  preview = false,
  initialSecurity,
}: {
  code: string;
  favorite: boolean;
  onFavorite: () => void;
  onBack: () => void;
  onMetric: (id: MetricId, row?: Stock) => void;
  preview?: boolean;
  initialSecurity?: Stock;
}) {
  const [snapshot, setData] = useState<Detail | null>(null), [error, setError] = useState("");
  const [summaryRetry, setSummaryRetry] = useState(0);
  const [extra, setExtra] = useState<Partial<Detail>>({});
  const [extraError, setExtraError] = useState("");
  const [extraBusy, setExtraBusy] = useState(false), [extraRetry, setExtraRetry] = useState(0);
  const [depthOpen, setDepthOpen] = useState(false);
  const [wideWorkspace, setWideWorkspace] = useState(false);
  const [depthWidth,setDepthWidth]=usePanelWidth('depth',280,220,360),uid=useId();
  useEffect(() => {
    const media = matchMedia("(min-width: 1280px)");
    const sync = () => { setWideWorkspace(media.matches); if (media.matches) setDepthOpen(false); };
    sync(); media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  const [analysisOpen,setAnalysisOpen] = useState(false);
  const [clock,setClock]=useState(0);
  useEffect(()=>{setClock(Date.now());const timer=setInterval(()=>setClock(Date.now()),30000);return()=>clearInterval(timer);},[]);
  const pendingSecurity:Stock=initialSecurity??{code,name:code,market:'',price:null,change:null,volume:null,value:null,cap:null,foreign:null,per:null,pbr:null,roe:null,dividend:null,foreign_net:null,institution_net:null,revenue:null,operating_profit:null,as_of:'',received_at:'',market_status:'',facts:{}};
  const data:Detail = snapshot ? {...snapshot, ...extra, security: snapshot.security,
    detail: {...snapshot.detail, ...extra.detail}} : {status:'pending',security:pendingSecurity,detail:{facts:{}},prices:{rows:[],adjustment:'unverified',source_trace:{source:'',source_url:'',period:'',unit:'',method:'',formula:''}},filings:{rows:[],status:'pending'}};
  const [selection, updateSelection, selectionReady, resetChart] = useDetailSelection(code, preview);
  const tab = selection.tab, chartMode = selection.mode;
  // Let shared/saved preferences resolve before choosing the first request.
  const firstChartPath=selectionReady&&(preview||tab==='요약'||tab==='차트')
    ? detailChartPath(code,selection) : null;
  const firstChart=useMemo(()=>firstChartPath?createFirstChartRequest(firstChartPath):undefined,[firstChartPath]);
  useEffect(()=>{
    if(!firstChart)return;
    const load=()=>{if(!document.hidden)firstChart.preload();};
    load();document.addEventListener('visibilitychange',load);
    return()=>{document.removeEventListener('visibilitychange',load);firstChart.cancel();};
  },[firstChart]);
  const setTab = (tab: string) => updateSelection({tab, ...(tab === "기업·주주" ? {analysis:"valuation"} : tab === "재무·지표" ? {analysis:"financials"} : {})});
  const analysisSection = tab === "재무·지표" ? "financials" : selection.analysis;
  const inAnalysis = !preview && ["기업·주주","재무·지표"].includes(tab);
  const setChartMode = (mode: string) => updateSelection({mode});
  const [frequency, setFrequency] = useState("annual"),
    [basis, setBasis] = useState("연결"),
    [estimates, setEstimates] = useState(false);
  const view = tab === "수급·거래원" ? "activity" : ["차트", "재무·지표"].includes(tab) || (tab === "기업·주주" && analysisSection === "estimates") ? "fundamentals" : tab === "기업·주주" ? "company" : tab === "공시·자료" ? "filings" : null;
  useEffect(() => {
    if (!view) return;
    const abort = new AbortController();
    const fetchSection = detailLoader(code, abort.signal, view);
    let busy = false;
    const load = async () => {
      if (busy || document.hidden) return;
      busy = true; setExtraBusy(true);
      try {
        const next = await fetchSection();
        if (abort.signal.aborted) return;
        if (next) setExtra(previous => ({...previous, detail: {...previous.detail, ...next.detail},
          ...(next.official ? {official: next.official} : {}), ...(view === "filings" ? {filings: next.filings} : {})}));
        setExtraError("");
      } catch { if (!abort.signal.aborted) setExtraError("자료를 불러오지 못했습니다."); }
      finally { busy = false; if (!abort.signal.aborted) setExtraBusy(false); }
    };
    void load();
    const timer = setInterval(load, 60000);
    document.addEventListener("visibilitychange", load);
    return () => { abort.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [code, view, extraRetry]);
  useEffect(() => {
    const abort = new AbortController();
    const loadSnapshot = detailLoader(code, abort.signal);
    let loaded = false, busy = false;
    const load = async () => {
      if (busy || (loaded && document.hidden)) return;
      busy = true;
      try {
        const next = await loadSnapshot();
        if (abort.signal.aborted) return;
        if (!next) { setError(""); return; }
        loaded = true;
        setData(previous => ({...next, prices: next.prices?.rows?.length ? next.prices : previous?.prices ?? next.prices,
          security: previous && previous.security.as_of > next.security.as_of ? previous.security : next.security}));
        setError("");
      } catch { if (!abort.signal.aborted) setError("종목 정보를 불러오지 못했습니다."); }
      finally { busy = false; }
    };
    void load();
    const timer = setInterval(load, 60000);
    const stopQuotes = subscribeQuotes([code], rows => {
      const quote=rows[0];
      if(quote) setData(previous => previous && ({...previous,
        security: previous.security.as_of > quote.as_of ? previous.security : quote}));
    });
    const visible = () => { if (!document.hidden) { void load(); } };
    document.addEventListener("visibilitychange", visible);
    return () => {
      abort.abort();
      clearInterval(timer); stopQuotes();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [code, summaryRetry]);
  const s = data?.security ?? initialSecurity,
    details: Detail["detail"] = data?.detail ?? {facts:{}},
    financials = (details.financials || []).filter(
      (f) =>
        f.frequency === frequency &&
        (f.basis === (basis === "연결" ? "CFS" : "OFS") || f.basis === basis) &&
        (estimates || !f.estimate),
    );
  const periods = [...new Set(financials.map((f) => f.period))],
    names = [...new Set(financials.map((f) => f.name))];
  const stats: MetricId[] = [
    "volume",
    "value",
    "cap",
    "foreign",
    "per",
    "pbr",
    "roe",
    "dividend",
  ];
  const tabs = [
    {label:"시세", target:"요약", members:["요약","차트"]},
    {label:"수급", target:"수급·거래원", members:["수급·거래원","공매도"]},
    {label:"기업 분석", target:"기업·주주", members:["기업·주주","재무·지표"]},
    {label:"공시·뉴스", target:"공시·자료", members:["공시·자료"]},
  ];
  const activeTab=tabs.findIndex(item=>item.members.includes(tab));
  return (
    <section className="mk-detail" aria-busy={!snapshot&&!error}>
      <div className="mk-security-header">
        <div>
          <div className="mk-eyebrow">
            {s.market} · {s.code}
          </div>
          <div className="mk-security-title-row">{!preview && <button className="mk-back" aria-label="목록으로 돌아가기" title="목록으로 돌아가기" onClick={onBack}><ArrowLeft size={20}/></button>}<h1 className="mk-company-title"><CompanyLogo url={s.logo_url} name={s.name} large/>{s.name}</h1></div>
          <div className="mk-security-price">
            {format(s.price)}
            <span>원</span>
            <b className={tone(s.change)}>{format(s.change, "change")}</b>
          </div>
          <p className="mk-caption">
            {clock && (!s.received_at || clock-Date.parse(s.received_at)>120000) ? `마지막 확인 가격${s.as_of ? ' · '+s.as_of.slice(0,10) : ''}` : s.market_status === "CLOSE" ? "장 마감" : s.market_status === "OPEN" ? "장중" : clock && s.as_of && s.as_of.slice(0,10) < new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul'}).format(clock) ? `${s.as_of.slice(0,10)} 가격` : ""}
          </p>
        </div>
        <button
          className={`mk-button ${favorite ? "mk-primary" : ""}`}
          aria-label={favorite ? "관심종목 저장됨" : "관심종목에 추가"}
          onClick={onFavorite}
        >
          <Star size={16} fill={favorite ? "currentColor" : "none"} />
          <span className="mk-interest-label-full">{favorite ? "관심종목 저장됨" : "관심종목에 추가"}</span>
          <span className="mk-interest-label-short" aria-hidden="true">{favorite ? "저장됨" : "관심"}</span>
        </button>
      </div>
      {!preview && <StockWarnings code={code}/>}
      {!preview && (
        <div className="mk-tabs mk-detail-nav-desktop" role="tablist" aria-label="종목 상세">
          {tabs.map((t) => (
            <button
              key={t.target}
              role="tab"
              id={`${uid}-${t.target}`}
              aria-controls={`${uid}-panel`}
              tabIndex={t.members.includes(tab)?0:-1}
              aria-selected={t.members.includes(tab)}
              onClick={() => setTab(t.target)}
              onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(activeTab+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;setTab(tabs[next].target);(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next])?.focus();}}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
      {(!snapshot||error)&&<div className="mk-loading mk-detail-summary-status" role={error?'alert':'status'}>{error||'종목 정보를 불러오는 중이에요.'}{error&&<button className="mk-button" onClick={()=>{setError('');setSummaryRetry(value=>value+1);}}>다시 시도</button>}</div>}
      {view && <div className="mk-detail-section-status" role="status">{extraError ? <>{extraError} <button onClick={()=>setExtraRetry(v=>v+1)}>다시 불러오기</button></> : extraBusy ? "자료를 불러오는 중…" : null}</div>}
      {!preview && ["수급·거래원","공매도"].includes(tab) && <div className="mk-section-navigation" aria-label="수급 상세">
        {[["flows","투자자"],["brokers","거래원"],["program","프로그램"],["short","공매도"],["financing","대차·신용"]].map(([key,label])=><button key={key} aria-pressed={key==="short"?tab==="공매도":tab==="수급·거래원"&&selection.activity===key} onClick={()=>key==="short"?setTab("공매도"):updateSelection({tab:"수급·거래원",activity:key})}>{label}</button>)}
      </div>}
      <div className={inAnalysis ? "mk-analysis-shell" : undefined} id={`${uid}-panel`} role={preview?undefined:'tabpanel'} aria-labelledby={preview?undefined:`${uid}-${tabs[Math.max(0,activeTab)].target}`}>
      {inAnalysis && <nav className="mk-analysis-navigation" aria-label="기업 분석 목차">
        {[["valuation","실적과 주가"],["financials","재무"],["dividends","배당"],["shareholders","주주"],["estimates","예상실적"],["peers","동종 비교"]].map(([key,label])=><button key={key} aria-current={analysisSection===key?"page":undefined} onClick={()=>updateSelection({tab:key==="financials"?"재무·지표":"기업·주주",analysis:key})}>{label}</button>)}
      </nav>}
      <div className={inAnalysis ? "mk-analysis-content" : undefined}>
      {(preview || tab === "요약" || tab === "차트") && (
        <div className="mk-detail-overview" data-depth={!preview&&wideWorkspace} style={{'--mk-depth-width':`${depthWidth}px`} as CSSProperties}>
          <div className="mk-panel mk-chart-panel">
            {selectionReady && (chartMode==='intraday'?<StockIntraday code={code} name={s.name} quoteDate={s.as_of} firstRequest={firstChart} onHistory={()=>setChartMode('history')} period={selection.period} kind={selection.kind} lineInterval={selection.lineInterval} onLineIntervalChange={lineInterval=>updateSelection({lineInterval})} onKindChange={kind=>updateSelection({kind,...(['candle','bar'].includes(kind)?{period:'day'}:{})})} onPeriodChange={period => updateSelection({period,...(period==='week'?{kind:'line'}:{})})}/>:<PriceChart code={code} firstRequest={firstChart} selection={selection} onSelectionChange={updateSelection} onIntraday={()=>setChartMode('intraday')}/>)}
            <div className="mk-workspace-chart-actions">{!preview && !wideWorkspace && <button className="mk-button" onClick={()=>setDepthOpen(true)}>호가·체결 보기</button>}<details className="mk-chart-options"><summary>차트 추가 설정</summary><button className="mk-button" onClick={resetChart}>기본 차트로</button></details></div>
            {!preview && <PriceContextPanel key={code} code={code} name={s.name}/>}
          </div>
          {!preview && wideWorkspace && <><PaneSeparator label="호가 너비" width={depthWidth} onWidth={setDepthWidth} initial={280} min={220} max={360} side="right" container=".mk-detail-overview"/><aside className="mk-workspace-depth"><DepthPanel code={code}/></aside></>}
          <div className="mk-panel mk-workspace-stats">
            <div className="mk-section-head">
              <h2>핵심 지표</h2>

            </div>
            <dl className="mk-stat-grid">
              {stats.map((k) => (
                <div key={k}>
                  <dt>
                    <button onClick={() => onMetric(k, s)}>
                      {
                        {
                          volume: "거래량",
                          value: "거래대금",
                          cap: "시가총액",
                          foreign: "외국인 지분율",
                          per: "PER",
                          pbr: "PBR",
                          roe: "ROE",
                          dividend: "배당수익률",
                        }[k as "volume"]
                      }
                      <Info size={13} />
                    </button>
                  </dt>
                  <dd>
                    {format(s[k], k)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
      {!preview&&tab==='요약'&&<><PerformanceStrip code={code}/>{snapshot&&<details className="mk-company-description"><summary>기업 개요</summary><p className="mk-company-summary">{details.company?.description||data.company?.products||'기업 설명이 없습니다.'}<a href={`https://wcomp.fnguide.com/CompanyInfo/Snapshot?cmp_cd=${code}`} target="_blank" rel="noreferrer"> 기업정보 더 보기 <ExternalLink size={13}/></a></p></details>}</>}
      {!preview && tab === "차트" && (
        <ValuationHistory code={code}/>
      )}
      {!preview && !wideWorkspace && depthOpen && <Dialog title="호가·체결" onClose={()=>setDepthOpen(false)}><DepthPanel code={code}/></Dialog>}
      {!preview&&tab==='재무·지표'&&<EarningsVisual code={code} financials={details.financials||[]}/>}
      {!preview&&tab==='재무·지표'&&<details className="mk-company-description" onToggle={event=>setAnalysisOpen(event.currentTarget.open)}><summary>주가·실적 비교와 계산기</summary>{analysisOpen&&<ValuationHistory code={code}/>}</details>}
      {!preview&&tab==='재무·지표'&&<TtmPanel code={code}/>}
      {!preview&&tab==='재무·지표'&&<OfficialStatements code={code} data={data.official||{company:{},statements:[]}}/>}
      {!preview && tab === "수급·거래원" && <ActivityPanel key={code} detail={details} code={code} section={selection.activity} holding={{value:s.foreign,date:s.facts?.foreign?.period}}/>}
      {!preview && tab === "공매도" && <ShortSellingPanel code={code}/>}
      {!preview && tab === "재무·지표" && (details.financials||[]).length>0 && (
        <div className="mk-panel">
          <div className="mk-section-head">
            <h2>재무와 투자 지표</h2>
            
          </div>
          <div className="mk-toolbar">
            <div className="mk-segment">
              {[
                ["annual", "연간"],
                ["quarter", "분기"],
              ].map(([v, l]) => (
                <button
                  key={v}
                  aria-pressed={frequency === v}
                  onClick={() => setFrequency(v)}
                >
                  {l}
                </button>
              ))}
            </div>
            <select
              aria-label="재무 연결 별도"
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
            >
              <option>연결</option>
              <option>별도</option>
            </select>
            <label className="mk-check">
              <input
                type="checkbox"
                checked={estimates}
                onChange={(e) => setEstimates(e.target.checked)}
              />
              추정치 포함
            </label>
          </div>
          {financials.length ? (
            <div className="mk-table-scroll">
              <table className="mk-data-table mk-financial">
                <caption className="mk-sr">
                  {basis} {frequency === "annual" ? "연간" : "분기"} 재무 지표.
                  E는 추정치입니다.
                </caption>
                <thead>
                  <tr>
                    <th>항목 · 단위</th>
                    {periods.map((p) => (
                      <th key={p}>
                        {p}
                        {financials.some((f) => f.period === p && f.estimate)
                          ? " (E)"
                          : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {names.map((n) => (
                    <tr key={n}>
                      <th>
                        {n}
                        <small>
                          {financialUnit(n, financials.find((f) => f.name === n)?.unit)}
                        </small>
                      </th>
                      {periods.map((p) => {
                        const f = financials.find(
                          (x) => x.name === n && x.period === p,
                        );
                        return (
                          <td
                            key={p}
                            className={f?.estimate ? "mk-estimate" : ""}
                          >
                            {format(f?.value, "raw")}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mk-empty">
              선택한 기준의 재무 자료가 없습니다. 기간이나 연결 기준을 바꿔보세요.
            </div>
          )}
          <p className="mk-caption">
            실적과 추정치를 구분합니다. TTM은 연속 4개 분기와 누적·단독 분기
            기준을 검증한 뒤 제공됩니다.
          </p>
        </div>
      )}
      {!preview && tab === "기업·주주" && (
        <>
          {analysisSection === "valuation" && <><ValuationHistory key={code} code={code}/><EarningsVisual code={code} financials={details.financials||[]}/><div className="mk-panel">
            <details className="mk-company-description"><summary>기업 소개 · {s.name}</summary>
            {data.official?.company&&<dl className="mk-company-facts"><div><dt>공시 법인명</dt><dd>{data.official.company.corp_name}</dd></div><div><dt>대표자</dt><dd>{data.official.company.ceo_nm||'—'}</dd></div><div><dt>설립일</dt><dd>{data.official.company.est_dt?.replace(/^(\d{4})(\d{2})(\d{2})$/,'$1.$2.$3')||'—'}</dd></div><div><dt>결산월</dt><dd>{data.official.company.acc_mt?`${data.official.company.acc_mt}월`:'—'}</dd></div></dl>}
            <p>
              {details.company?.description ||
                `${s.name} · ${s.market} 상장 종목`}
            </p>
            <p className="mk-caption">
              {s.sector || "업종 정보 없음"}
            </p>
            <a
              className="mk-text-link"
              href={`https://wcomp.fnguide.com/CompanyInfo/Snapshot?cmp_cd=${code}`}
              target="_blank"
              rel="noreferrer"
            >
              기업정보 더 보기 <ExternalLink size={14} />
            </a>
            </details>
          </div></>}
          {analysisSection === "estimates" && <EstimatesPanel financials={details.financials||[]}/>}
          {analysisSection === "peers" && <PeersPanel code={code}/>}
          {analysisSection === "dividends" && <div id="company-dividends"><DartCompanyFacts facts={data.official?.report_facts} only="dividends"/><EventsPanel key={`events-${code}`} code={code}/></div>}
          {analysisSection === "shareholders" && <DartCompanyFacts facts={data.official?.report_facts} only="shareholders"/>}
          {analysisSection === "shareholders" && !data.official?.report_facts?.sections.shareholders?.rows?.length && <div className="mk-panel">
            <div className="mk-section-head">
              <h2>주요 주주</h2>
              <span className="mk-caption">보통주 · 주주별 최종 변동일</span>
            </div>
            <div className="mk-table-scroll">
              <table className="mk-data-table">
                <thead>
                  <tr>
                    <th>주주명</th>
                    <th>보유 주식</th>
                    <th>지분율</th>
                    <th>기준일</th>
                  </tr>
                </thead>
                <tbody>
                  {(details.shareholders || []).map((h, i) => (
                    <tr key={i}>
                      <th>{h.name}</th>
                      <td>{format(h.shares)}</td>
                      <td>{format(h.ratio, "foreign")}</td>
                      <td>{h.as_of}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!details.shareholders?.length && (
              <div className="mk-empty">주주 정보 미제공</div>
            )}
          </div>}
        </>
      )}
      </div></div>
      {!preview && tab === "공시·자료" && <DisclosuresPanel key={code} code={code} filings={data.filings.rows} research={details.research||[]}/>}

    </section>
  );
}
