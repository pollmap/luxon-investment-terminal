"use client";
import {useEffect, useState} from "react";
import {format, tone} from "./types";
import {useVisibleQuery} from "./use-visible-query";
import {LinePlot, type Series} from "./market-pulse";
import {ContextDateControls,CalendarTable,ListingTable,type MarketContext} from "./market-reference";
import {captureRankingOrder,rankingView,marketDataFailed,type Ranking,type RankingOrder} from "./market-data-state";

const ranks=[['MARKET_TRADING_AMOUNT','시장 거래대금'],['MARKET_TRADING_VOLUME','시장 거래량'],['TOP_GAINERS','상승률'],['TOP_LOSERS','하락률'],['TOSS_SECURITIES_TRADING_AMOUNT','토스 고객 거래대금'],['TOSS_SECURITIES_TRADING_VOLUME','토스 고객 거래량']];
const durations=[['realtime','실시간'],['1d','하루'],['1w','1주'],['1mo','1개월'],['3mo','3개월'],['6mo','6개월'],['1y','1년']];
const groups=[['individual','개인'],['foreigner','외국인'],['institution','기관'],['otherCorporation','기타법인']] as const;
const institutionGroups=[['financialInvestment','금융투자'],['insurance','보험'],['trust','투신'],['privateEquityFund','사모펀드'],['bank','은행'],['otherFinancialInstitution','기타금융'],['pensionFund','연기금']] as const;
type Investor={institutions?:Record<string,{buy:number|null;sell:number|null;net:number|null}>;date:string;individual?:{buy:number|null;sell:number|null;net:number|null};foreigner?:Investor['individual'];institution?:Investor['individual'];otherCorporation?:Investor['individual']};

function useMarketData<T>(path:string,initial:T){
  const result=useVisibleQuery<T>(path,15000);
  return {...result,data:result.data??initial};
}

function MarketRankings({onSelect}:{onSelect:(code:string)=>void}){
  const [kind,setKind]=useState(ranks[0][0]),[duration,setDuration]=useState('1d'),[exclude,setExclude]=useState(false);
  const {data,error:failed,retry}=useMarketData<Ranking>(`/rankings?type=${kind}&duration=${duration}&exclude_caution=${exclude}`,{rows:[],status:'pending'});
  const [frozen,setFrozen]=useState<RankingOrder|null>(null);
  const key=`${kind}:${duration}:${exclude}`;
  const error=marketDataFailed(data.status,failed);
  const view=rankingView(data,key,frozen);
  const [visible,setVisible]=useState(20);
  useEffect(()=>{setFrozen(previous=>captureRankingOrder(previous,key,data));},[data,key]);
  return <section aria-label="기간별 순위">
    <div className="mk-toolbar"><label>순위 <select value={kind} onChange={e=>{setKind(e.target.value);if(e.target.value.startsWith('TOP_')&&duration==='realtime')setDuration('1d');}}>{ranks.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <label>기간 <select value={duration} onChange={e=>setDuration(e.target.value)}>{durations.map(([value,label])=><option key={value} value={value} disabled={value==='realtime'&&kind.startsWith('TOP_')}>{label}</option>)}</select></label>
      <label><input type="checkbox" checked={exclude} onChange={e=>setExclude(e.target.checked)}/> 투자주의 종목 제외</label>
      <button className="mk-button" onClick={()=>{setFrozen(captureRankingOrder(null,key,data));retry();}}>최신 순위 반영</button>
    </div>
    {error&&<p role="status">{view.rows.length?'순위를 갱신하지 못했어요. 마지막 자료예요.':'순위를 불러오지 못했어요.'} <button onClick={retry}>다시 시도</button></p>}{!view.rows.length?(!error&&<p role="status">{view.status==='pending'?'선택한 순위를 가져오는 중이에요.':'표시할 종목이 없어요.'}</p>):<>
      <p className="mk-caption">{view.scope} · 등락률은 {view.change_basis} 대비{view.as_of&&<> · <time dateTime={view.as_of}>{view.as_of.slice(0,16).replace('T',' ')}</time></>}</p>
      <div className="mk-table-scroll" style={{maxHeight:560}}><table className="mk-data-table"><thead><tr><th>순위</th><th>종목</th><th>현재가</th><th>등락률</th><th>거래대금</th><th>거래량</th></tr></thead><tbody>{view.rows.slice(0,visible).map(row=><tr key={row.code}><td>{row.rank}</td><th><button onClick={()=>onSelect(row.code)}>{row.name}</button>{row.unranked&&<span className="mk-caption"> · 순위에서 제외</span>}</th><td>{format(row.price,'price')}</td><td className={tone(row.change)}>{format(row.change,'change')}</td><td>{format(row.value,'value')}</td><td>{format(row.volume,'volume')}</td></tr>)}</tbody></table></div>{view.rows.length>visible&&<button className="mk-button" onClick={()=>setVisible(v=>v+20)}>20개 더 보기</button>}</>}
  </section>;
}

function MarketInvestors(){
  const [code,setCode]=useState('KOSPI'),[interval,setIntervalValue]=useState('1d'),[value,setValue]=useState<'buy'|'sell'|'net'>('net');
  const [visible,setVisible]=useState(30),[breakdown,setBreakdown]=useState(false);
  const columns=breakdown?institutionGroups:groups;
  const {data,error,retry}=useMarketData<{rows:Investor[];status:string;total_rows?:number}>(`/indices/${code}/investors?interval=${interval}&limit=${visible}`,{rows:[],status:'pending'});
  const failed=marketDataFailed(data.status,error);
  return <section><div className="mk-toolbar"><label>시장 <select value={code} onChange={e=>setCode(e.target.value)}><option value="KOSPI">코스피</option><option value="KOSDAQ">코스닥</option></select></label><label>집계 <select value={interval} onChange={e=>setIntervalValue(e.target.value)}>{[['1d','일별'],['1w','주별'],['1mo','월별'],['1y','연별']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><div className="mk-segment">{([['net','순매수'],['buy','매수'],['sell','매도']] as const).map(([v,l])=><button key={v} aria-pressed={value===v} onClick={()=>setValue(v)}>{l}</button>)}</div><label className="mk-check"><input type="checkbox" checked={breakdown} onChange={e=>setBreakdown(e.target.checked)}/>기관 세부</label><span className="mk-caption">KRX · 억원</span></div>
    {failed&&<p role="status">{data.rows.length?'시장 수급을 갱신하지 못했어요. 마지막 자료예요.':'시장 수급을 불러오지 못했어요.'} <button onClick={retry}>다시 시도</button></p>}
    <div className="mk-table-scroll" style={{maxHeight:560}}><table className="mk-data-table"><thead><tr><th>기준일</th>{columns.map(([g,l])=><th key={g}>{l}</th>)}</tr></thead><tbody>{[...data.rows].reverse().slice(0,visible).map(row=><tr key={row.date}><th>{row.date}</th>{columns.map(([g])=>{const n=(breakdown?row.institutions?.[g]:row[g as typeof groups[number][0]])?.[value];return <td key={g} className={value==='net'?tone(n):''}>{n==null?'—':(n/1e8).toLocaleString('ko-KR',{maximumFractionDigits:1})}</td>;})}</tr>)}</tbody></table>{!data.rows.length&&!failed&&<p role="status">{data.status==='pending'?'시장 수급을 가져오는 중이에요.':'조회된 시장 수급이 없어요.'}</p>}{(data.total_rows??data.rows.length)>visible&&<button className="mk-button" onClick={()=>setVisible(n=>n+30)}>이전 30개 더 보기</button>}</div>
  </section>;
}

function MarketReference(){
  const [query,setQuery]=useState('');
  const {data,error,retry}=useMarketData<MarketContext>(`/market-context${query?'?'+query:''}`,{});
  const [bond,setBond]=useState('KR_BOND_10Y');
  const {data:chart}=useMarketData<Series>(`/indices/${bond}/chart?period=year`,{points:[],status:'pending'});
  return <section><ContextDateControls onApply={setQuery}/>{error&&<p role="status">불러오지 못했어요. <button onClick={retry}>다시 시도</button></p>}
    <dl className="mk-stat-grid"><div><dt>달러 환율 · 매매기준율</dt><dd>{data.exchange_rate?.rate?.toLocaleString('ko-KR',{maximumFractionDigits:6})??'—'} <small>{data.exchange_rate?.unit||'원/USD'}</small></dd></div>{data.indicators?.rows?.filter(r=>r.unit==='%').map(r=><div key={r.code}><dt>국채 {r.code.replace('KR_BOND_','').replace('Y','년')}</dt><dd>{r.price??'—'} <small>%</small></dd></div>)}</dl>
    <label>금리 추이 <select value={bond} onChange={e=>setBond(e.target.value)}>{[2,3,5,10,20,30].map(year=><option key={year} value={`KR_BOND_${year}Y`}>국채 {year}년</option>)}</select></label>
    <LinePlot data={chart} unit="%" label="국채 금리 1년 추이"/>
    <h3>거래 일정</h3><CalendarTable calendars={data.calendars}/>
  </section>;
}

export default function MarketData({onSelect}:{onSelect:(code:string)=>void}){
  const [open,setOpen]=useState(false),[section,setSection]=useState('rankings');
  return <details className="mk-company-description" onToggle={e=>setOpen(e.currentTarget.open)}><summary>기간별 순위 · 시장 수급 · 금리·환율</summary>{open&&<><div className="mk-section-navigation" aria-label="시장 자료">{[['rankings','기간별 순위'],['investors','시장 수급'],['reference','금리·환율·일정'],['listings','상장 현황']].map(([key,label])=><button key={key} aria-pressed={section===key} onClick={()=>setSection(key)}>{label}</button>)}</div>{section==='rankings'?<MarketRankings onSelect={onSelect}/>:section==='investors'?<MarketInvestors/>:section==='listings'?<ListingTable onSelect={onSelect}/>:<MarketReference/>}</>}</details>;
}

export function StockWarnings({code}:{code:string}){
  const {data,error,retry}=useMarketData<{rows:{name:string;exchange?:string}[];status?:string;limits?:{upper?:number;lower?:number};stock?:{koreanMarketDetail?:{krxTradingSuspended?:boolean;nxtTradingSuspended?:boolean}}}>(`/securities/${code}/warnings`,{rows:[]});
  const [delayed,setDelayed]=useState(false);
  useEffect(()=>{setDelayed(false);if(data.status!=='pending')return;const timer=setTimeout(()=>setDelayed(true),10000);return()=>clearTimeout(timer);},[code,data.status]);
  const stock=data.stock?.koreanMarketDetail;
  const failed=error||data.status==='error'||data.status==='stale';
  const hasLast=!!data.rows.length||!!stock||data.limits?.upper!=null;
  return <>{(failed||delayed)&&<div className="mk-inline-state mk-caption" role="status" aria-label="종목 유의사항 조회 상태">{delayed&&!failed?'종목 유의사항 확인이 늦어지고 있어요.':hasLast?'종목 유의사항을 갱신하지 못했어요. 마지막 자료예요.':'종목 유의사항을 불러오지 못했어요.'}<button className="mk-button" aria-label="유의사항 다시 시도" onClick={retry}>다시 시도</button></div>}{!!data.rows.length&&<div className="mk-notice" role="status">{data.rows.map((r,i)=><span key={i}>{r.name}{r.exchange?` (${r.exchange})`:''} </span>)}</div>}{(stock?.krxTradingSuspended||stock?.nxtTradingSuspended)&&<p role="status">{stock.krxTradingSuspended?'KRX ':''}{stock.nxtTradingSuspended?'NXT ':''}거래정지</p>}{data.limits?.upper!=null&&<div className="mk-caption">상한가 {format(data.limits.upper,'price')}원 · 하한가 {format(data.limits.lower,'price')}원</div>}</>;
}
