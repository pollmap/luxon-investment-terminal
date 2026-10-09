"use client";
import { useEffect, useState } from "react";
import Dialog from "./dialog";
import { format, request, tone } from "./types";

type Article = { title: string; url: string; publisher?: string | null; published_at?: string | null; securities?: {code:string;name:string}[] };
type News = {status: string; rows: Article[]; has_more?: boolean};
type Brief = {status:string; date:string|null; available_dates:string[]; groups:{venue:string;covered:number;value:number|null;breadth:{up:number;down:number;flat:number;unknown:number};rows:{code:string;name:string;price:number|null;change:number|null;value:number|null}[]}[]; news?:News};
type Relation = {relation_id:string;object_code:string;name:string;kind:string;review_status:string;evidence:{url:string;excerpt:string;published_at:string}};

function useContextData<T>(path: string, revision: number) {
  const [state,setState] = useState<{path:string;data:T|null;error:boolean;busy:boolean}>({path:"",data:null,error:false,busy:true});
  useEffect(() => {
    const abort = new AbortController();
    setState(previous => ({path,data: previous.path === path ? previous.data : null,error:false,busy:true}));
    request<T>(path,{signal:AbortSignal.any([abort.signal,AbortSignal.timeout(15000)])})
      .then(data => {if (!abort.signal.aborted) setState({path,data,error:false,busy:false});})
      .catch(() => {if (!abort.signal.aborted) setState(previous => ({...previous,error:true,busy:false}));});
    return () => abort.abort();
  },[path,revision]);
  return state.path === path ? state : {path,data:null,error:false,busy:true};
}

function Articles({rows,onOpen}:{rows:Article[];onOpen:(code:string)=>void}) {
  return <div>{rows.map(article => <div className="mk-news-item" key={article.url}>
    <a className="mk-document" href={article.url} target="_blank" rel="noreferrer"><strong>{article.title}</strong><small>{[article.publisher,article.published_at?.slice(0,16).replace("T"," ")].filter(Boolean).join(" · ")} ↗</small></a>
    {!!article.securities?.length && <div className="mk-news-companies" aria-label="기사 관련 종목">{article.securities.slice(0,4).map(stock => <button key={stock.code} onClick={() => onOpen(stock.code)}>{stock.name}</button>)}</div>}
  </div>)}</div>;
}

function MarketHistory({onOpen}:{onOpen:(code:string)=>void}) {
  const [date,setDate] = useState("");
  const [revision,setRevision] = useState(0);
  const history = useContextData<Brief>("/market-brief"+(date ? `?date=${date}` : ""),revision);
  const selectedDate = date || history.data?.date || "";
  const dates = history.data?.available_dates ?? [];
  const index = dates.indexOf(selectedDate);
  const today = new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Seoul"}).format(new Date());
  const venues: Record<string,string> = {KRX:"KRX",NXT:"NXT",SOR:"통합",INTEGRATED:"통합",unspecified:"거래시장 구분 미제공"};
  return <section aria-label="날짜별 시장 기록">
    <div className="mk-history-controls"><label>날짜 <input type="date" value={selectedDate} max={today} onChange={event=>setDate(event.target.value)}/></label>
      <button className="mk-button" disabled={index < 0 || index >= dates.length-1} onClick={()=>setDate(dates[index+1])}>이전 기록</button>
      <button className="mk-button" disabled={index <= 0} onClick={()=>setDate(dates[index-1])}>다음 기록</button>
      <button className="mk-button" onClick={()=>setDate("")}>최근 기록</button></div>
    {history.error && <p role="status">불러오지 못했어요. <button className="mk-button" onClick={()=>setRevision(value=>value+1)}>다시 시도</button></p>}
    {!history.data && history.busy && <p role="status">시장 기록을 불러오는 중이에요.</p>}
    {history.data?.status === "not_collected" && <p role="status">이 날짜의 시장 기록은 아직 없어요.</p>}
    {history.data?.groups.map(group=><section key={group.venue} aria-label={venues[group.venue] || group.venue}>
      <div className="mk-section-head"><h3>{venues[group.venue] || group.venue}</h3><span className="mk-caption">기록된 {group.covered.toLocaleString()}종목</span></div>
      <p className="mk-history-breadth">상승 <b className="mk-up">{group.breadth.up.toLocaleString()}</b> 하락 <b className="mk-down">{group.breadth.down.toLocaleString()}</b> 보합 <b>{group.breadth.flat.toLocaleString()}</b></p>
      <div className="mk-history-table"><table><caption>거래대금 순위 · {history.data?.date}</caption><thead><tr><th scope="col">종목</th><th scope="col">가격 (원)</th><th scope="col">등락률</th><th scope="col">거래대금</th></tr></thead><tbody>{group.rows.slice(0,10).map(row=><tr key={row.code}><th scope="row"><button onClick={()=>onOpen(row.code)}>{row.name}</button></th><td>{format(row.price)}</td><td className={tone(row.change)}>{format(row.change,"change")}</td><td>{format(row.value,"value")}</td></tr>)}</tbody></table></div>
    </section>)}
    {!!history.data?.news?.rows.length && <><h3>그날의 관련 뉴스</h3><Articles rows={history.data.news.rows} onOpen={onOpen}/></>}
  </section>;
}

export default function MarketJournal({onOpen}:{onOpen:(code:string)=>void}) {
  const [historyOpen,setHistoryOpen] = useState(false),[revision,setRevision] = useState(0),[limit,setLimit]=useState(6);
  const news = useContextData<News>(`/news?limit=${limit}`,revision);
  return <section className="mk-panel mk-market-journal" aria-label="시장 소식">
    <div className="mk-section-head"><h2>관련 소식</h2><button className="mk-button" onClick={()=>setHistoryOpen(true)}>날짜별 시장</button></div>
    {news.error && <p role="status">뉴스를 불러오지 못했어요. <button className="mk-button" onClick={()=>setRevision(value=>value+1)}>다시 시도</button></p>}
    {!news.data && news.busy && <p role="status">뉴스를 불러오는 중이에요.</p>}
    {news.data && !news.data.rows.length && <p>{news.data.status === "not_collected" ? "아직 저장된 시장 뉴스가 없어요." : "연결된 시장 뉴스가 없어요."}</p>}
    {news.data && <Articles rows={news.data.rows} onOpen={onOpen}/>}
    {news.data?.has_more && limit < 30 && <button className="mk-button" onClick={()=>setLimit(value=>value+6)}>뉴스 더 보기</button>}
    {historyOpen && <Dialog title="날짜별 시장" onClose={()=>setHistoryOpen(false)} wide><MarketHistory onOpen={code=>{setHistoryOpen(false);onOpen(code);}}/></Dialog>}
  </section>;
}

export function RelatedCompanies({code,onOpen}:{code:string;onOpen?:(code:string)=>void}) {
  const [revision,setRevision] = useState(0);
  const relations = useContextData<{status:string;rows:Relation[]}>(`/securities/${code}/relations`,revision);
  const labels: Record<string,string> = {supplier:"공급 기업",customer:"고객 기업",subsidiary:"자회사",shareholder:"주주",business_partner:"사업 관계",shared_news:"같은 관련 기사"};
  const rows = (relations.data?.rows ?? []).filter(row=>row.kind === "shared_news" ? row.review_status === "source_observed" : row.review_status === "reviewed");
  return <section className="mk-related-companies" aria-label="관련 기업과 근거"><h3>관련 기업</h3>
    {relations.error && <p role="status">관련 기업을 불러오지 못했어요. <button className="mk-button" onClick={()=>setRevision(value=>value+1)}>다시 시도</button></p>}
    {!relations.data && relations.busy && <p role="status">관련 자료를 불러오는 중이에요.</p>}
    {!relations.error && relations.data && !rows.length && <p>확인된 관련 기업이 없어요.</p>}
    {rows.slice(0,12).map(row=><article key={row.relation_id}><div>{onOpen ? <button className="mk-button" onClick={()=>onOpen(row.object_code)}>{row.name}</button> : <a href={`/stocks?code=${row.object_code}`}>{row.name}</a>}<span className="mk-caption">{labels[row.kind] || "관련 기업"}</span></div>
      <a className="mk-document" href={row.evidence.url} target="_blank" rel="noreferrer"><strong>{row.evidence.excerpt}</strong><small>{row.evidence.published_at.slice(0,10)} · 근거 보기 ↗</small></a>
    </article>)}
  </section>;
}
