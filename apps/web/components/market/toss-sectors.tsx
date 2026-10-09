"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, ChevronRight, Search } from "lucide-react";
import Dialog from "./dialog";
import CompanyLogo from "./company-logo";
import { useExplorationState } from "./exploration-state";
import { format, request, tone } from "./types";

type Snapshot = { status?: string; rows?: Sector[] };
type Sector = { id: string; name: string; rank: number; change: number | null; value: number | null };
type Industry = Pick<Sector, "id" | "name">;
type CatalogueRow = Industry & { parent_id: string | null; depth: number };
type Catalogue = { status?: string; rows?: CatalogueRow[] };
type Members = { status?: string; rows?: { code: string; name: string; market?: string; cap: number | null; value: number | null }[] };
type Description = { status?: string; summary?: string; performance?: { duration: string; change: number | null }[] };
const periods = [
  { value: "1d", label: "하루" }, { value: "1w", label: "1주" },
  { value: "1mo", label: "1개월" }, { value: "3mo", label: "3개월" },
  { value: "6mo", label: "6개월" }, { value: "1y", label: "1년" },
];
type Selection = {kind: string; duration: string; filter: string; limit: number; view?: "ranking" | "catalogue"; parentId?: string | null};
const initialSelection: Selection = {kind:"MARKET_TRADING_AMOUNT",duration:"1d",filter:"",limit:10,view:"ranking",parentId:null};
function isSelection(value: unknown): value is Selection {
  if (!value || typeof value !== "object") return false;
  const row = value as Selection;
  return ["MARKET_TRADING_AMOUNT","TOP_GAINERS"].includes(row.kind) && periods.some(p => p.value === row.duration) && typeof row.filter === "string" && row.filter.length <= 200 && Number.isInteger(row.limit) && row.limit >= 10 && row.limit <= 10000 && (row.view === undefined || ["ranking","catalogue"].includes(row.view)) && (row.parentId == null || typeof row.parentId === "string" && /^[A-Za-z0-9_-]{1,40}$/.test(row.parentId));
}

function ancestry(row: CatalogueRow | undefined, byId: Map<string, CatalogueRow>): CatalogueRow[] {
  const trail: CatalogueRow[] = [], seen = new Set<string>();
  while (row && !seen.has(row.id)) {
    seen.add(row.id); trail.unshift(row);
    row = row.parent_id ? byId.get(row.parent_id) : undefined;
  }
  return trail;
}

function useSnapshot<T extends { status?: string }>(path: string | null, revision: number) {
  const [state, setState] = useState<{
    path: string | null; data: T | null; error: boolean; busy: boolean; delayed: boolean;
  }>({ path: null, data: null, error: false, busy: false, delayed: false });
  useEffect(() => {
    if (!path) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let loading = false, attempts = 0, waiting = true;
    const load = async () => {
      if (document.hidden || loading || abort.signal.aborted) return;
      clearTimeout(timer);
      loading = true;
      setState(previous => ({
        path, data: previous.path === path ? previous.data : null,
        error: false, busy: true, delayed: false,
      }));
      try {
        const data = await request<T>(path, { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]) });
        if (abort.signal.aborted) return;
        const failed = data.status === "error";
        const pending = data.status === "pending";
        attempts++;
        waiting = pending;
        setState(previous => ({
          path, data: failed && previous.path === path && previous.data?.status !== "pending" ? previous.data : data,
          error: failed, busy: false, delayed: pending && attempts >= 6,
        }));
        // Poll only while collection is pending; a ready ranking never reorders itself.
        if (pending && attempts < 6) timer = setTimeout(load, Math.min(attempts * 5000, 15000));
      } catch {
        waiting = false;
        if (!abort.signal.aborted) setState(previous => ({
          path, data: previous.path === path ? previous.data : null,
          error: true, busy: false, delayed: false,
        }));
      } finally { loading = false; }
    };
    const visible = () => {
      if (document.hidden) clearTimeout(timer);
      else if (waiting && attempts < 6) void load();
    };
    void load();
    document.addEventListener("visibilitychange", visible);
    return () => { abort.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [path, revision]);
  return state.path === path ? state : { path, data: null, error: false, busy: true, delayed: false };
}

export default function TossSectors({ onOpen }: { onOpen: (code: string) => void }) {
  const [selection,setSelection,restored] = useExplorationState("korea-stocks:industries:v1",initialSelection,isSelection);
  const {kind,duration,filter,limit} = selection;
  const view = selection.view ?? (filter ? "catalogue" : "ranking");
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [selected, setSelected] = useState<Industry | null>(null);
  const ranking = useSnapshot<Snapshot>(restored && view === "ranking" ? `/sectors/rankings?type=${kind}&duration=${duration}` : null, revision);
  const catalogue = useSnapshot<Catalogue>(restored && (view === "catalogue" || selected) ? "/sectors/catalogue" : null, revision);
  const description = useSnapshot<Description>(selected ? `/sectors/${encodeURIComponent(selected.id)}` : null, detailRevision);
  const members = useSnapshot<Members>(selected ? `/sectors/${encodeURIComponent(selected.id)}/stocks` : null, detailRevision);
  const rows = (ranking.data?.rows ?? []).filter(row => row.name.includes(filter.trim()));
  const byId = useMemo(() => new Map((catalogue.data?.rows ?? []).map(row => [row.id, row])), [catalogue.data]);
  const children = useMemo(() => {
    const map = new Map<string | null, CatalogueRow[]>();
    for (const row of byId.values()) map.set(row.parent_id, [...(map.get(row.parent_id) ?? []), row]);
    for (const group of map.values()) group.sort((a,b) => a.name.localeCompare(b.name,"ko"));
    return map;
  }, [byId]);
  const parent = selection.parentId ? byId.get(selection.parentId) : undefined;
  const trail = ancestry(parent,byId);
  const query = filter.trim().toLocaleLowerCase();
  const catalogueRows = query
    ? [...byId.values()].filter(row => ancestry(row,byId).some(item => item.name.toLocaleLowerCase().includes(query))).sort((a,b) => a.name.localeCompare(b.name,"ko"))
    : children.get(parent?.id ?? null) ?? [];
  const active = view === "catalogue" ? catalogue : ranking;
  const waiting = !active.data || active.data.status === "pending";
  const openBranch = (parentId: string | null) => setSelection(previous => ({...previous,view:"catalogue",parentId,filter:"",limit:10}));
  const switchView = (next: "ranking" | "catalogue") => setSelection(previous => ({...previous,view:next,filter:"",limit:10}));
  const retry = () => setRevision(value => value + 1);
  return <section className="mk-toss-sectors" aria-label="토스 산업 분류" aria-busy={!restored}>
    <fieldset className="mk-exploration-inputs" disabled={!restored} aria-label="산업 탐색 조작">
    <div className="mk-segment mk-industry-view" role="group" aria-label="산업 보기"><button aria-pressed={view === "ranking"} onClick={() => switchView("ranking")}>순위</button><button aria-pressed={view === "catalogue"} onClick={() => switchView("catalogue")}>전체 산업</button></div>
    <div className="mk-sector-controls mk-industry-controls">
      <label className="mk-theme-search"><Search size={17} aria-hidden="true" /><input aria-label="산업 검색" value={filter} maxLength={200} onChange={event => setSelection(previous => ({...previous,filter:event.target.value,view:"catalogue",parentId:null,limit:10}))} placeholder="전체 산업 검색" /></label>
      {view === "ranking" && <>
      <select aria-label="산업 순위" value={kind} onChange={event => setSelection(previous => ({...previous,kind:event.target.value,limit:10}))}><option value="MARKET_TRADING_AMOUNT">거래대금순</option><option value="TOP_GAINERS">상승률순</option></select>
      <select aria-label="산업 기간" value={duration} onChange={event => setSelection(previous => ({...previous,duration:event.target.value}))}>{periods.map(period => <option key={period.value} value={period.value}>{period.label}</option>)}</select>
      <button className="mk-button" disabled={ranking.busy} onClick={retry}>최신 순위 반영</button></>}
    </div>
    {(active.error || active.delayed || active.data?.status === "stale") && <p role="status" className="mk-inline-state">
      {active.delayed ? "자료 수집이 늦어지고 있어요." : active.data?.rows?.length ? "업데이트하지 못했어요. 마지막 자료를 보여드려요." : "불러오지 못했어요."}
      <button className="mk-button" disabled={active.busy} onClick={retry}>다시 시도</button>
    </p>}
    {!active.error && !active.delayed && waiting ? <p role="status">산업 자료를 가져오는 중이에요.</p> :
      !waiting && !(view === "catalogue" ? catalogueRows : rows).length && !active.error ? <p role="status">{filter ? "일치하는 산업이 없어요." : "조회된 산업이 없어요."}</p> : null}
    {view === "ranking" && !!rows.length && <div className="mk-industry-list">{rows.slice(0, limit).map(row => <button key={row.id} onClick={() => setSelected(row)}>
      <span className="mk-industry-rank">{row.rank}</span><span className="mk-industry-name"><strong>{row.name}</strong><small>거래대금 {format(row.value, "value")}</small></span>
      <b className={tone(row.change)}>{format(row.change, "change")}</b><ArrowUpRight size={16} aria-hidden="true" />
    </button>)}</div>}
    {view === "catalogue" && !!byId.size && <>
      <div className="mk-catalogue-heading"><nav className="mk-industry-path" aria-label="산업 분류 경로"><button onClick={() => openBranch(null)}>전체 산업 {byId.size}개</button>{!query && trail.map(item => <span key={item.id}><ChevronRight size={14} aria-hidden="true"/><button aria-current={item.id === parent?.id ? "page" : undefined} onClick={() => openBranch(item.id)}>{item.name}</button></span>)}</nav>{parent && !query && <button className="mk-button" onClick={() => setSelected(parent)}>이 산업 보기</button>}</div>
      <p className="mk-caption">{query ? `검색 결과 ${catalogueRows.length}개` : parent ? `하위 산업 ${catalogueRows.length}개` : `상위 산업 ${catalogueRows.length}개`}</p>
      <div className="mk-industry-catalogue">{catalogueRows.slice(0,limit).map(row => <div key={row.id}>
        <button className="mk-catalogue-open" onClick={() => setSelected(row)}><strong>{row.name}</strong>{query && row.parent_id && <small>{ancestry(row,byId).slice(0,-1).map(item => item.name).join(" › ")}</small>}<span>설명·구성 종목 <ArrowUpRight size={14} aria-hidden="true"/></span></button>
        {!!children.get(row.id)?.length && <button className="mk-catalogue-children" aria-label={`${row.name} 하위 산업 ${children.get(row.id)!.length}개 보기`} onClick={() => openBranch(row.id)}>하위 산업 {children.get(row.id)!.length}개 <ChevronRight size={16} aria-hidden="true"/></button>}
      </div>)}</div>
    </>}
    {(view === "catalogue" ? catalogueRows : rows).length > limit && <button className="mk-button" onClick={() => setSelection(previous => ({...previous,limit:previous.limit+10}))}>산업 더 보기 ({view === "catalogue" ? catalogueRows.length : rows.length})</button>}
    <p className="mk-caption">토스 산업 분류 · 한 기업이 여러 산업에 포함될 수 있어요.</p>
    </fieldset>
    {selected && <Dialog title={selected.name} onClose={() => setSelected(null)} wide>
      {byId.has(selected.id) && <nav className="mk-industry-path" aria-label="선택한 산업 분류 경로">{ancestry(byId.get(selected.id),byId).map((item,index) => <span key={item.id}>{index > 0 && <ChevronRight size={14} aria-hidden="true"/>}<button aria-current={item.id === selected.id ? "page" : undefined} onClick={() => setSelected(item)}>{item.name}</button></span>)}</nav>}
      {!!children.get(selected.id)?.length && <div className="mk-industry-children"><h3>하위 산업</h3>{children.get(selected.id)!.map(child => <button className="mk-button" key={child.id} onClick={() => setSelected(child)}>{child.name}<ChevronRight size={14} aria-hidden="true"/></button>)}</div>}
      {description.data?.summary && <p>{description.data.summary}</p>}
      {!!description.data?.performance?.length && <dl className="mk-industry-periods">{description.data.performance.map(row => <div key={row.duration}><dt>{periods.find(period => period.value === row.duration)?.label ?? row.duration}</dt><dd className={tone(row.change)}>{format(row.change, "change")}</dd></div>)}</dl>}
      <h3>구성 종목</h3>
      {(members.error || description.error || members.delayed || description.delayed || members.data?.status === "stale" || description.data?.status === "stale") && <p role="status" className="mk-inline-state">일부 자료를 불러오지 못했어요. <button className="mk-button" disabled={members.busy || description.busy} onClick={() => setDetailRevision(value => value + 1)}>다시 시도</button></p>}
      {!members.error && !members.delayed && (!members.data || members.data.status === "pending") && <p role="status">종목을 가져오는 중이에요.</p>}
      {!!members.data?.rows?.length && <div className="mk-member-list">{members.data.rows.map(row => <button key={row.code} onClick={() => { setSelected(null); onOpen(row.code); }}>
        <CompanyLogo name={row.name} /><span><strong>{row.name}</strong><small>{row.code} · {row.market}</small></span><span>시가총액 {format(row.cap, "cap")}</span>
      </button>)}</div>}
      {members.data?.status === "ready" && !members.data.rows?.length && <p>조회된 구성 종목이 없어요.</p>}
      <p className="mk-caption">하위 산업의 보통주 포함</p>
    </Dialog>}
  </section>;
}
