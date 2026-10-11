"use client";
import {subscribeQuotes} from "./quote-subscription";
import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  Search,
  Settings2,
  ALargeSmall,
  SlidersHorizontal,
  Columns3,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  RefreshCw,
  Star,
  LayoutGrid,
  GitCompareArrows,
  Check,
  X,
  Info,
  Sun,
  Moon,
  Monitor,
  Bookmark,
  ChevronLeft,
  ChevronRight,
  Plus,
  Trash2,
  WifiOff,
  Menu,
} from "lucide-react";
import Dialog, {consumeDialogCloseNavigation} from "./dialog";
import {detailRouteKeys, writeMarketHistory} from "./market-history";
import StockTable from "./stock-table";
import WorkspaceList from "./workspace-list";
import HeaderSearch from "./header-search";
import {useStockSearch} from "./use-stock-search";
import type {SearchIdentity} from "./search-index";
import MobileList from "./mobile-list";
import MarketPulse from "./market-pulse";
import MarketData from "./market-data";
import MarketExplorer from "./market-explorer";
const MarketJournal = dynamic(() => import("./market-journal"), {loading: () => null});
import CompanyLogo from "./company-logo";
import PortablePreferences, {ExportStocks} from "./portable-preferences";
const StockDetail = dynamic(() => import("./detail"), {
  loading: () => <div className="mk-loading" role="status">종목 화면을 불러오는 중…</div>,
});
const ComparisonChart = dynamic(() => import("./comparison-chart"), {
  loading: () => <div className="mk-loading" role="status">비교 차트를 불러오는 중…</div>,
});
import {
  defaults,
  defaultQuery,
  labels,
  presets,
  request,
  restorePreferences,
  restoreQuery,
  format,
  tone,
  type Stock,
  type MetricId,
  type Overview,
  type Preferences,
  type Query,
  type Rule,
  type Metric,
} from "./types";

type Page = "market" | "screener" | "watchlist" | "compare";
type Result = {
  rows: Stock[];
  total: number;
};
type SearchStock = SearchIdentity &
  Partial<
    Pick<
      Stock,
      "price" | "change" | "sector" | "as_of" | "received_at" | "market_status" | "source_trace"
    >
  > & {
    facts?: Partial<Stock["facts"]>;
  };
const sections: [Page, string, typeof Search][] = [
  ["market", "시장", LayoutGrid],
  ["screener", "종목 찾기", Search],
  ["watchlist", "관심종목", Star],
  ["compare", "비교", GitCompareArrows],
];
const storageKey = "luxon.public-market.v1";
function readRoute() {
  const p = new URLSearchParams(location.search);
  let query = defaultQuery;
  try {
    const raw = JSON.parse(p.get("filters") || "null");
    if (raw && typeof raw === "object") query = restoreQuery(raw);
  } catch {}
  return {
    page: (["market", "screener", "watchlist", "compare"].includes(
      p.get("page") || "",
    )
      ? p.get("page")
      : "market") as Page,
    code: /^[0-9A-Z]{6}$/.test(p.get("code") || "") ? p.get("code")! : null,
    query,
    selected: String(p.get("compare") || (Array.isArray(history.state?.marketSelection)?history.state.marketSelection.join(','):'') || "")
      .split(",")
      .filter((c) => /^[0-9A-Z]{6}$/.test(c))
      .slice(0, 4),
  };
}

export default function Dashboard() {
  const [prefs, setPrefs] = useState<Preferences>(defaults),
    [ready, setReady] = useState(false),
    [page, setPage] = useState<Page>("market"),
    [code, setCode] = useState<string | null>(null),
    [query, setQuery] = useState<Query>(defaultQuery);
  const [data, setData] = useState<Result | null>(null),
    [overview, setOverview] = useState<Overview | null>(null),
    [savedRows, setSavedRows] = useState<Stock[]>([]),
    [compareRows, setCompareRows] = useState<Stock[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [comparisonMode, setComparisonMode] = useState(false),
    [offset, setOffset] = useState(0),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [offline, setOffline] = useState(false);
  const [modal, setModal] = useState<string | null>(null),
    [metric, setMetric] = useState<{ id: MetricId; row?: Stock } | null>(null),
    [metrics, setMetrics] = useState<Record<string, Metric>>({}),
    [toast, setToast] = useState("");
  const [search, setSearch] = useState(""),
    [filterDraft, setFilterDraft] = useState<Query>(defaultQuery),
    [columnsDraft, setColumnsDraft] = useState<MetricId[]>(defaults.columns),
    [widthsDraft, setWidthsDraft] = useState<Record<string, number>>({}),
    [viewName, setViewName] = useState(""),
    [group, setGroup] = useState("전체"),
    [newGroup, setNewGroup] = useState(""),
    [mobileTable, setMobileTable] = useState(false),
    [rankPending, setRankPending] = useState(false),
    [retry, setRetry] = useState(0);
  const queryRef = useRef(query),
    comparisonReturn=useRef<{page:Page;offset:number;scroll:number}>({page:'screener',offset:0,scroll:0}),
    routeSearch = useRef(""),
    rowsRef = useRef<Stock[]>([]),
    scrollRef = useRef(0),
    requestId = useRef(0),
    loadedListKey = useRef(""),
    dataRef = useRef<Result | null>(null),
    focusCode = useRef<string | null>(null);
  const {rows:results,busy:searchBusy,failed:searchFailed,retry:retrySearch}=useStockSearch(search,modal==="search",30);
  const [canUndoFavorite,setCanUndoFavorite] = useState(false);
  const undoFavorite = useRef<(() => void) | null>(null);
  const lastFavoriteTouch = useRef({code:"",time:0});
  const notify = useCallback((message: string) => { undoFavorite.current = null; setCanUndoFavorite(false); setToast(message); }, []);
  const openSearch = useCallback(() => {
    setSearch("");
    setModal("search");
  }, []);
  useEffect(() => {
    let initialPage: Page = "market";
    try {
      const restored = restorePreferences(JSON.parse(localStorage.getItem(storageKey) || "null"));
      setPrefs(restored);
      initialPage = restored.startPage || "market";
    } catch {
      notify("저장된 설정을 읽지 못해 기본 보기로 열었습니다.");
    }
    const r = readRoute();
    setPage(location.search ? r.page : initialPage);
    setCode(r.code);
    setQuery(r.query);
    setSelected(r.selected);
    routeSearch.current = location.search;
    setReady(true);
    setOffline(!navigator.onLine);
    const pop = () => {
      if(consumeDialogCloseNavigation()){writeMarketHistory("replace",location.pathname+routeSearch.current);return;}
      if (routeSearch.current === location.search) return;
      const n = readRoute();
      setPage(n.page);
      setCode(n.code);
      setQuery(n.query);
      setSelected(n.selected);
      requestAnimationFrame(() => window.scrollTo(0, n.code ? 0 : scrollRef.current));
    };
    const on = () => setOffline(false),
      off = () => setOffline(true);
    const detailRoute = () => { routeSearch.current = location.search; };
    window.addEventListener("market-route-change", detailRoute);
    window.addEventListener("popstate", pop);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    request<Record<string, Metric>>("/metrics")
      .then(setMetrics)
      .catch(() => {});
    if ("serviceWorker" in navigator)
      navigator.serviceWorker
        .register("/stocks/sw.js", { scope: "/stocks" })
        .catch(() => {});
    return () => {
      window.removeEventListener("popstate", pop);
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      window.removeEventListener("market-route-change", detailRoute);
    };
  }, [notify]);
  useEffect(() => {
    if (!ready) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(prefs));
    } catch {
      notify(
        "저장 공간이 부족합니다. 변경 내용이 이 기기에 저장되지 않았습니다.",
      );
    }
  }, [prefs, ready, notify]);
  useEffect(() => {
    if (!toast || canUndoFavorite) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast, canUndoFavorite]);
  useEffect(() => {
    queryRef.current = query;
  }, [query]);
  useEffect(() => {
    if (!ready) return;
    const p = new URLSearchParams();
    if (page !== "market") p.set("page", page);
    if (code) p.set("code", code);
    if (code && new URLSearchParams(location.search).get("code") === code)
      for (const key of detailRouteKeys)
        if (new URLSearchParams(location.search).has(key)) p.set(key, new URLSearchParams(location.search).get(key)!);
    if (JSON.stringify(query) !== JSON.stringify(defaultQuery))
      p.set("filters", JSON.stringify(query));
    if (page === "compare" && selected.length)
      p.set("compare", selected.join(","));
    writeMarketHistory(
      "replace",
      location.pathname + (p.size ? "?" + p.toString() : ""),
      {marketSelection:selected,marketOffset:offset},
    );
    routeSearch.current = location.search;
  }, [page, code, query, selected, ready,offset]);
  const visibleCodes =
    page === "watchlist"
      ? group === "전체"
        ? prefs.favorites
        : prefs.groups[group] || []
      : undefined;
  const codeKey = visibleCodes?.join(",");
  const streamCodes =
    data?.rows
      .slice(0, 40)
      .map((s) => s.code)
      .join(",") || "";
  useEffect(() => {
    if (!ready || code || page === "compare") return;
    const body=JSON.stringify({
      ...query,offset,limit:page==="market"?10:40,
      ...(codeKey!==undefined?{codes:codeKey?codeKey.split(","):[]}:{}),
    });
    const listKey=`${page}:${retry}:${body}`;
    // A detail round trip resumes the existing order instead of re-ranking it.
    if(loadedListKey.current===listKey)return;
    const abort=new AbortController();
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    setRankPending(false);
    request<Result>("/screener", {
      method: "POST",
      signal:abort.signal,
      body,
    })
      .then((d) => {
        if (!abort.signal.aborted && id === requestId.current) {
          loadedListKey.current=listKey;
          setData(d);
          setError("");
          dataRef.current = d;
          rowsRef.current = d.rows;
          setLoading(false);
        }
      })
      .catch(() => {
        if (!abort.signal.aborted && id === requestId.current) {
          setError("종목 목록을 불러오지 못했습니다.");
          setLoading(false);
        }
      });
    return()=>abort.abort();
  }, [ready, page, code, query, offset, codeKey, retry]); // codes are represented by codeKey
  useEffect(() => {
    if (!ready || code || page!=="market") return;
    let active = true,busy=false;const abort=new AbortController();
    const load = () => {
      if(document.hidden||busy)return;
      busy=true;
      return request<Overview>("/overview",{signal:abort.signal})
        .then((d) => {
          if (active) setOverview(d);
        })
        .catch(() => {}).finally(()=>{busy=false;});
    };
    load();
    const timer = setInterval(load, 60000);
    document.addEventListener("visibilitychange",load);
    return () => {
      active = false;
      abort.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange",load);
    };
  }, [ready, code, page, retry]);
  useEffect(() => {
    const codes = [...new Set([...prefs.favorites, ...prefs.recent])];
    if (!ready || !codes.length) {
      setSavedRows([]);
      return;
    }
    let active = true;
    request<Result>("/screener", {
      method: "POST",
      body: JSON.stringify({ codes, limit: 100 }),
    })
      .then((d) => {
        if (active) setSavedRows(d.rows);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [ready, prefs.favorites, prefs.recent]);
  useEffect(() => {
    if (page !== "compare" || !selected.length) return;
    let active = true;
    request<Result>("/screener", {
      method: "POST",
      body: JSON.stringify({ codes: selected, limit: 4 }),
    })
      .then((d) => {
        if (active) setCompareRows(d.rows);
      })
      .catch((e) => notify(e.message));
    return () => {
      active = false;
    };
  }, [page, selected, notify]);
  useEffect(() => {
    if (!ready || code || page==="compare" || !streamCodes || offline) return;
    let active = true;
    const codes = streamCodes.split(",");
    const merge = (rows: Stock[]) => {
      if (!active) return;
      const map = new Map(rows.map((s) => [s.code, s]));
      const changed = dataRef.current?.rows.some((s) => {
        const next = map.get(s.code);
        return next && next[queryRef.current.sort] !== s[queryRef.current.sort];
      });
      setData((previous) => {
        if (!previous) return previous;
        const updated = {
          ...previous,
          rows: previous.rows.map((s) => {const next=map.get(s.code);return next && next.as_of >= s.as_of ? next : s;}),
        };
        dataRef.current = updated;
        return updated;
      });
      if (changed) setRankPending(true);
    };
    const stop = subscribeQuotes(codes, merge);
    return () => { active = false; stop(); };
  }, [ready, code, page, streamCodes, offline]);
  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        openSearch();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [openSearch]);
  const updatePrefs = (partial: Partial<Preferences>) =>
    setPrefs((p) => ({ ...p, ...partial }));
  const navigate = (p: Page) => {
    if(p==='compare'&&page!=='compare')comparisonReturn.current={page,offset,scroll:window.scrollY};
    setPage(p);
    setCode(null);
    setOffset(0);
    window.scrollTo(0, 0);
  };
  const returnFromComparison=()=>{const saved=comparisonReturn.current;setPage(saved.page);setOffset(saved.offset);requestAnimationFrame(()=>window.scrollTo(0,saved.scroll));};
  // This workspace owns list restoration; browser restoration must not race it.
  useEffect(() => {
    const previous = history.scrollRestoration;
    history.scrollRestoration = 'manual';
    return () => { history.scrollRestoration = previous; };
  }, []);
  const open = (c: string) => {
    if (c === code) return;
    if (!code) scrollRef.current = window.scrollY;
    focusCode.current = c;
    const next = new URL(location.href);
    if (next.searchParams.get("code") !== c)
      detailRouteKeys.forEach(key => next.searchParams.delete(key));
    next.searchParams.set("code", c);
    // A search dialog already owns an entry. Reuse it so Back returns
    // directly to the list, and never enqueue the old URL in the Next router.
    writeMarketHistory(history.state?.__marketDialog ? "replace" : "push", next, {__marketDialog:undefined});
    routeSearch.current = location.search;
    setCode(c);
    setModal(null);
    setPrefs((p) => ({
      ...p,
      recent: [c, ...p.recent.filter((x) => x !== c)].slice(0, 12),
    }));
    requestAnimationFrame(() => window.scrollTo(0, 0));
  };
  const back = () => {
    setCode(null);
    requestAnimationFrame(() => {
      window.scrollTo(0, scrollRef.current);
      const target = [
        ...document.querySelectorAll<HTMLButtonElement>(
          `[data-code="${focusCode.current}"]`,
        ),
      ].find((el) => el.getClientRects().length > 0);
      target?.focus({ preventScroll: true });
    });
  };
  const favorite = (c: string) => {
    const time = Date.now();
    if (lastFavoriteTouch.current.code === c && time - lastFavoriteTouch.current.time < 600) return;
    lastFavoriteTouch.current = {code:c,time};
    const removing = prefs.favorites.includes(c);
    const previousGroups = Object.keys(prefs.groups).filter(name => prefs.groups[name].includes(c));
    setPrefs((p) => ({
      ...p,
      favorites: removing
        ? p.favorites.filter((x) => x !== c)
        : [...p.favorites, c],
      groups: removing
        ? Object.fromEntries(
            Object.entries(p.groups).map(([n, cs]) => [
              n,
              cs.filter((x) => x !== c),
            ]),
          )
        : p.groups,
    }));
    notify(
      removing
        ? "관심종목에서 해제했습니다."
        : "이 기기의 관심종목에 저장했습니다.",
    );
    setCanUndoFavorite(true);
    undoFavorite.current = () => {
      setPrefs(p => ({...p,
        favorites: removing ? [...new Set([...p.favorites, c])] : p.favorites.filter(x => x !== c),
        groups: Object.fromEntries(Object.entries(p.groups).map(([name, codes]) => [name,
          removing && previousGroups.includes(name) ? [...new Set([...codes, c])] : removing ? codes : codes.filter(x => x !== c)])),
      }));
      undoFavorite.current = null;
      setCanUndoFavorite(false);
      setToast("관심종목 변경을 취소했습니다.");
    };
  };
  const select = (c: string) =>
    setSelected((p) =>
      p.includes(c) ? p.filter((x) => x !== c) : p.length < 4 ? [...p, c] : p,
    );
  const onMetric = (id: MetricId, row?: Stock) => setMetric({ id, row });
  const recentSuggestions = prefs.recent
    .map((recentCode) =>
      savedRows.find((row) => row.code === recentCode) ||
      data?.rows.find((row) => row.code === recentCode),
    )
    .filter((row): row is Stock => !!row)
    .slice(0, 8);
  const suggestedRows = recentSuggestions.length
    ? recentSuggestions
    : (data?.rows || []).slice(0, 8);
  const visibleSearchRows: SearchStock[] = search.trim() ? results : suggestedRows;
  const sort = (key: MetricId) => {
    setOffset(0);
    setQuery((q) => ({
      ...q,
      sort: key,
      direction: q.sort === key && q.direction === "desc" ? "asc" : "desc",
    }));
  };
  useEffect(()=>{if(!error||offline)return;const timer=setTimeout(()=>setRetry(n=>n+1),10000);return()=>clearTimeout(timer);},[error,offline,retry]);
  const displayColumns =
    page === "market"
      ? ([
          "price",
          "change",
          ...(query.sort === "foreign_net"
            ? ["foreign_net", "institution_net"]
            : query.sort === "cap"
              ? ["cap", "value"]
              : ["value", "volume"]),
        ] as MetricId[])
      : prefs.columns;
  const savedItem = (s: Stock) => (
    <div className="mk-watch-row" key={s.code}>
      <button onClick={() => open(s.code)}>
        <CompanyLogo url={s.logo_url} name={s.name}/><strong>{s.name}</strong>
        <small>{s.code}</small>
      </button>
      <div>
        <b>{format(s.price)}</b>
        <small className={tone(s.change)}>{format(s.change, "change")}</small>
      </div>
    </div>
  );
  const contextSidebar = (<aside className="mk-sidebar">
                    {page === "market" && !code && <MarketJournal onOpen={open}/>}
                    <section className="mk-panel">
                      <div className="mk-section-head">
                        <h2>내 관심종목</h2>
                        {prefs.favorites.length > 0 && <button
                          className="mk-icon"
                          aria-label="관심종목 추가"
                          onClick={openSearch}
                        >
                          <Plus size={17} />
                        </button>}
                      </div>
                      {prefs.favorites.length ? (
                        savedRows
                          .filter((s) => prefs.favorites.includes(s.code))
                          .slice(0, 6)
                          .map(savedItem)
                      ) : (
                        <div className="mk-side-empty">

                          <button
                            className="mk-button"
                            onClick={openSearch}
                          >
                            관심종목 추가
                          </button>
                        </div>
                      )}
                      {prefs.favorites.length > 0 && <button
                        className="mk-side-link"
                        onClick={() => navigate("watchlist")}
                      >
                        관심종목 전체 보기
                        <ArrowRight size={14} />
                      </button>}
                    </section>
                    <section className="mk-panel">
                      <div className="mk-section-head">
                        <h2>최근 본 종목</h2>
                      </div>
                      {prefs.recent.length ? (
                        savedRows
                          .filter((s) => prefs.recent.includes(s.code))
                          .sort(
                            (a, b) =>
                              prefs.recent.indexOf(a.code) -
                              prefs.recent.indexOf(b.code),
                          )
                          .slice(0, 5)
                          .map(savedItem)
                      ) : (
                        <p className="mk-muted mk-side-note">
                          확인한 종목이 이곳에 모입니다.
                        </p>
                      )}
                    </section>
                  </aside>);
  const ruleLabel = (r: Rule) =>
    `${labels[r.metric]} ${r.operator === "gte" ? "≥" : r.operator === "lte" ? "≤" : "="} ${r.value.toLocaleString()}${metrics[r.metric]?.unit || ""}`;
  const activeMetric = metric ? metrics[metric.id] : undefined;
  const fact = metric?.row?.facts?.[metric.id];
  return (
    <div
      className={`mk-app ${prefs.sidebarHidden ? "mk-sidebar-hidden" : ""} ${prefs.textSize === "larger" ? "mk-largest" : prefs.large || prefs.textSize === "large" ? "mk-large" : ""} ${prefs.compact ? "mk-compact" : ""} ${mobileTable ? "mk-full-table" : ""}`}
      data-theme={prefs.theme}
    >
      <a className="mk-skip" href="#market-main">
        본문으로 바로가기
      </a>
      <header className="mk-header">
        <div className="mk-header-inner">
          <button
            className="mk-brand"
            onClick={() => navigate("market")}
            aria-label="주식 장보기 홈"
          >
            <img src="/brand/jusikdam-symbol.png" width="36" height="36" alt="" />
            주식 장보기
          </button>
          <nav className="mk-desktop-nav" aria-label="주 메뉴">
            {sections.map(([id, name]) => (
              <button
                key={id}
                aria-label={name}
                aria-current={page === id && !code ? "page" : undefined}
                onClick={() => navigate(id)}
              >
                {name}
                {id === "watchlist" && prefs.favorites.length > 0 && (
                  <small>{prefs.favorites.length}</small>
                )}
              </button>
            ))}
          </nav>
          <HeaderSearch onOpen={open}/>{!code && <button className="mk-button mk-sidebar-toggle" aria-pressed={!prefs.sidebarHidden} onClick={()=>updatePrefs({sidebarHidden:!prefs.sidebarHidden})}>관심 목록 {prefs.sidebarHidden?"열기":"접기"}</button>}
          <button className="mk-button mk-text-size" aria-label="글자 크기" onClick={() => setModal("text-size")}><span className="mk-text-size-label">글자 크기</span><ALargeSmall className="mk-text-size-glyph" size={22} aria-hidden="true"/></button>
          <button
            className="mk-icon"
            aria-label="화면 설정"
            onClick={() => setModal("settings")}
          >
            <Settings2 size={19} />
          </button>
        </div>
      </header>
      {(offline || error) && (
        <div className="mk-connection" role="status">
          <WifiOff size={16} />
          {offline
            ? "인터넷 연결이 끊겼습니다. 화면에 불러온 정보를 계속 표시합니다."
            : error}
          <button onClick={() => setRetry((n) => n + 1)}>다시 연결</button>
        </div>
      )}
      <main id="market-main" className="mk-main">
        {!ready ? <div className="mk-panel" style={{minHeight:420}} role="status" aria-busy="true">화면을 여는 중…</div> : code ? (
          <div className="mk-detail-workspace"><WorkspaceList rows={data?.rows ?? []} code={code} favorites={prefs.favorites} onOpen={open} onFavorite={favorite}/><StockDetail
            key={code}
            code={code}
            initialSecurity={data?.rows.find(row=>row.code===code) ?? savedRows.find(row=>row.code===code)}
            favorite={prefs.favorites.includes(code)}
            onFavorite={() => favorite(code)}
            onBack={back}
            onMetric={onMetric}
          /></div>
        ) : (
          <>
            <div className="mk-page-heading">
              <div>
                
                <h1>
                  {page === "market"
                    ? "오늘의 시장"
                    : page === "screener"
                      ? "종목 찾기"
                      : page === "watchlist"
                        ? "내 관심종목"
                        : "종목 비교"}
                </h1>

              </div>
              <div className="mk-heading-actions">
                {page === "market" ? (
                  <button
                    className="mk-button mk-home-edit"
                    onClick={() => setModal("home")}
                  >
                    <Settings2 size={15} />홈 편집
                  </button>
                ) : page === "watchlist" ? (
                  <>
                    <button
                      className="mk-button"
                      onClick={() => setModal("groups")}
                    >
                      그룹 관리
                    </button>
                    <button
                      className="mk-button"
                      onClick={openSearch}
                    >
                      <Plus size={16} />
                      종목 추가
                    </button>
                  </>
                ) : page === "compare" ? (
                  <button
                    className="mk-button"
                    onClick={returnFromComparison}
                  >
                    읽던 목록으로 돌아가기
                  </button>
                ) : (
                  <button
                    className="mk-button"
                    onClick={() => {
                      setViewName("");
                      setModal("save");
                    }}
                  >
                    <Bookmark size={16} />
                    보기 저장
                  </button>
                )}
              </div>
            </div>
            {page === "market" && (
              <>
                <MarketPulse overview={overview}/>
                <div className="mk-market-strip">
                  {overview?.indices?.[0]?.as_of && <time className="mk-market-date" dateTime={overview.indices[0].as_of.slice(0,10)}>{overview.indices[0].as_of.slice(0,10)}</time>}
                  <span className="mk-market-status">
                    <i />
                    {!overview?.indices?.length ? "시장 확인 중" : overview.indices.every((i) => i.market_status === "CLOSE")
                      ? "장 마감"
                      : overview.indices.some((i) => i.market_status === "OPEN") ? "장중" : "시장 확인 중"}
                  </span>
                  <span>
                    상승{" "}
                    <b className="mk-up">
                      {overview?.breadth?.up?.toLocaleString() || "—"}
                    </b>
                  </span>
                  <span>
                    하락{" "}
                    <b className="mk-down">
                      {overview?.breadth?.down?.toLocaleString() || "—"}
                    </b>
                  </span>
                  <span className="mk-strip-flat">
                    보합 <b>{overview?.breadth?.flat?.toLocaleString() || "—"}</b>
                  </span>
                  <span className="mk-strip-value">
                    거래대금 <b>{format(overview?.value, "value")}원</b>
                  </span>
                </div>
              </>
            )}
            {page === "compare" ? (
              <div className="mk-panel">
                {selected.length >= 2 && <ComparisonChart codes={selected} />}
                {selected.length < 2 ? (
                  <div className="mk-empty">
                    <GitCompareArrows size={30} />
                    <h2>어떤 종목을 비교할까요?</h2>
                    <p>종목 목록에서 체크박스로 2~4개를 선택하세요.</p>
                    <button
                      className="mk-button mk-primary"
                      onClick={() => navigate("screener")}
                    >
                      종목 찾기
                    </button>
                  </div>
                ) : (
                  <div className="mk-table-scroll">
                    <table className="mk-data-table mk-compare">
                      <thead>
                        <tr>
                          <th>비교 지표</th>
                          {compareRows.map((s) => (
                            <th key={s.code}>
                              <button onClick={() => open(s.code)}>
                                {s.name} ↗
                              </button>
                              <small>
                                {s.market} · {s.code}
                              </small>
                              <button
                                className="mk-small-link"
                                onClick={() => select(s.code)}
                              >
                                선택 해제
                              </button>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(Object.keys(labels) as MetricId[]).map((k) => (
                          <tr key={k}>
                            <th>
                              <button onClick={() => onMetric(k)}>
                                {labels[k]} <Info size={12} />
                              </button>
                              {["price", "volume", "foreign_net", "institution_net", "revenue", "operating_profit"].includes(k) && <small className="mk-comparison-unit">{metrics[k]?.unit}</small>}
                            </th>
                            {compareRows.map((s) => (
                              <td key={s.code}>
                                <button
                                  onClick={() => onMetric(k, s)}
                                  className={k === "change" ? tone(s[k]) : ""}
                                >
                                  {format(s[k], k)}
                                </button>
                                <small>
                                  {["per", "pbr", "roe", "dividend", "revenue", "operating_profit"].includes(k) ? s.facts[k]?.period?.split("T")[0] || "" : ""}
                                </small>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="mk-caption">
                  각 값의 기준일을 함께 확인하세요. 서로 다른 결산 기간의 수치를
                  같은 기간으로 바꾸지 않습니다.
                </p>
              </div>
            ) : (
              <div className={page === "market" ? "mk-home-grid" : ""}>
                <div className="mk-primary-column">
                  {(page !== "market" || prefs.home.includes("ranking")) && (
                    <section className="mk-panel mk-list-panel">
                      <div className="mk-section-head">
                        <h2>
                          {page === "market"
                            ? `${query.sort === "change" ? query.direction === "asc" ? "하락률" : "상승률" : labels[query.sort]} 상위`
                            : page === "watchlist"
                              ? "저장한 종목"
                              : "전체 종목"}
                          <span className="mk-count">
                            {page === "market"
                              ? data ? `전체 ${data.total.toLocaleString()}개` : ""
                              : data?.total.toLocaleString() || 0}
                          </span>
                        </h2>
                        {loading && <span className="mk-caption mk-update-label">불러오는 중</span>}
                      </div>
                      {page === "market" ? (
                        <div className="mk-toolbar mk-rank-controls">
                          <div className="mk-segment">
                            {(
                              [
                                "value",
                                "cap", "change", "volume",
                              ] as MetricId[]
                            ).map((k) => (
                              <button
                                key={k}
                                aria-pressed={query.sort === k && query.direction === "desc"}
                                onClick={() => {
                                  setOffset(0);
                                  setQuery((q) => ({
                                    ...q,
                                    sort: k,
                                    direction: "desc",
                                  }));
                                }}
                              >
                                {k === "foreign_net"
                                  ? "외국인 수급"
                                  : k === "change" ? "상승률" : labels[k]}
                              </button>
                            ))}
                            <button aria-pressed={query.sort === "change" && query.direction === "asc"} onClick={()=>{setOffset(0);setQuery(q=>({...q,sort:"change",direction:"asc"}));}}>하락률</button>
                          </div>
                          <button className="mk-button" onClick={() => setModal("ranking")}>순위 변경</button>
                          <select
                            aria-label="시장 선택"
                            value={query.market}
                            onChange={(e) => {
                              setQuery((q) => ({
                                ...q,
                                market: e.target.value,
                              }));
                              setOffset(0);
                            }}
                          >
                            <option value="ALL">전체 시장</option>
                            <option>KOSPI</option>
                            <option>KOSDAQ</option>
                          </select>
                          <button className="mk-button mk-rank-compare" aria-pressed={comparisonMode || selected.length > 0} onClick={() => {const active = comparisonMode || selected.length > 0;if(active) setSelected([]);setComparisonMode(!active);}}>{comparisonMode || selected.length ? "비교 취소" : "비교 선택"}</button>
                        </div>
                      ) : (
                        <>
                          <div className="mk-toolbar">
                            <div className="mk-segment">
                              {Object.entries(presets).map(([name, cols]) => (
                                <button
                                  key={name}
                                  aria-pressed={
                                    prefs.columns.join() === cols.join()
                                  }
                                  onClick={() => updatePrefs({ columns: cols })}
                                >
                                  {name}
                                </button>
                              ))}
                            </div>
                            <div className="mk-toolbar-end">
                              <ExportStocks query={query} codes={page === "watchlist" ? (group === "전체" ? prefs.favorites : prefs.groups[group] || []) : undefined} notify={notify} />
                              <button
                                className="mk-button"
                                onClick={() => {
                                  setFilterDraft(structuredClone(query));
                                  setModal("filters");
                                }}
                              >
                                <SlidersHorizontal size={15} />
                                필터
                                {query.rules.length + query.any_rules.length >
                                  0 && (
                                  <b>
                                    {query.rules.length +
                                      query.any_rules.length}
                                  </b>
                                )}
                              </button>
                              <button
                                className="mk-button"
                                onClick={() => {
                                  setColumnsDraft([...prefs.columns]);
                                  setWidthsDraft({ ...prefs.widths });
                                  setModal("columns");
                                }}
                              >
                                <Columns3 size={15} />열 편집
                              </button>
                              <button
                                className="mk-button mk-mobile-only"
                                onClick={() => setMobileTable((v) => !v)}
                              >
                                {mobileTable ? "간단 보기" : "표 보기"}
                              </button>
                            </div>
                          </div>
                          <div className="mk-toolbar mk-sub-toolbar">
                            <label className="mk-inline-search">
                              <Search size={15} />
                              <input
                                placeholder="목록에서 종목 찾기"
                                aria-label="목록에서 종목 찾기"
                                value={query.q}
                                onChange={(e) => {
                                  setQuery((q) => ({
                                    ...q,
                                    q: e.target.value,
                                  }));
                                  setOffset(0);
                                }}
                              />
                            </label>
                            <select
                              aria-label="시장 선택"
                              value={query.market}
                              onChange={(e) => {
                                setQuery((q) => ({
                                  ...q,
                                  market: e.target.value,
                                }));
                                setOffset(0);
                              }}
                            >
                              <option value="ALL">전체 시장</option>
                              <option>KOSPI</option>
                              <option>KOSDAQ</option>
                            </select>
                            {page === "watchlist" && (
                              <select
                                aria-label="관심종목 그룹"
                                value={group}
                                onChange={(e) => setGroup(e.target.value)}
                              >
                                <option>전체</option>
                                {Object.keys(prefs.groups).map((g) => (
                                  <option key={g}>{g}</option>
                                ))}
                              </select>
                            )}
                            {prefs.views.length > 0 && (
                              <select
                                aria-label="저장한 보기"
                                value=""
                                onChange={(e) => {
                                  const v = prefs.views.find(
                                    (x) => x.id === e.target.value,
                                  );
                                  if (v) {
                                    setQuery(v.query);
                                    updatePrefs({
                                      columns: v.columns,
                                      widths: v.widths,
                                    });
                                    setOffset(0);
                                    notify(`${v.name} 보기를 적용했습니다.`);
                                  }
                                }}
                              >
                                <option value="">저장한 보기</option>
                                {prefs.views.map((v) => (
                                  <option key={v.id} value={v.id}>
                                    {v.name}
                                  </option>
                                ))}
                              </select>
                            )}
                          </div>
                        </>
                      )}
                      {(query.rules.length > 0 ||
                        query.any_rules.length > 0 ||
                        query.q ||
                        query.sector ||
                        query.market !== "ALL") && (
                        <div className="mk-filter-chips">
                          {query.sector && (
                            <button
                              onClick={() =>
                                setQuery((q) => ({ ...q, sector: "" }))
                              }
                            >
                              {query.sector}
                              <X size={12} />
                            </button>
                          )}
                          {query.q && <span>검색: {query.q}</span>}
                          {query.market !== "ALL" && (
                            <span>{query.market}</span>
                          )}
                          {query.rules.map((r, i) => (
                            <button
                              key={"and" + i}
                              onClick={() =>
                                setQuery((q) => ({
                                  ...q,
                                  rules: q.rules.filter((_, n) => n !== i),
                                }))
                              }
                            >
                              {ruleLabel(r)}
                              <X size={12} />
                            </button>
                          ))}
                          {query.any_rules.map((r, i) => (
                            <button
                              key={"or" + i}
                              onClick={() =>
                                setQuery((q) => ({
                                  ...q,
                                  any_rules: q.any_rules.filter(
                                    (_, n) => n !== i,
                                  ),
                                }))
                              }
                            >
                              OR {ruleLabel(r)}
                              <X size={12} />
                            </button>
                          ))}
                          <button
                            className="mk-reset"
                            onClick={() => {
                              setQuery(defaultQuery);
                              setOffset(0);
                            }}
                          >
                            전체 초기화
                          </button>
                        </div>
                      )}
                      {loading && !data ? (
                        <div
                          className="mk-skeleton"
                          role="status"
                          aria-label="종목 목록 불러오는 중"
                        >
                          {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                            <i key={i} />
                          ))}
                        </div>
                      ) : data?.rows.length ? (
                        <>
                          <MobileList
                            rows={data.rows}
                            selectionMode={page === "market" ? comparisonMode : undefined}
                            favorites={prefs.favorites}
                            selected={selected}
                            metric={
                              page === "market"
                                ? query.sort
                                : prefs.columns.find(
                                    (k) => !["price", "change"].includes(k),
                                  ) || "value"
                            }
                            onOpen={open}
                            onFavorite={favorite}
                            onSelect={select}
                            onMetric={onMetric}
                          />
                          <StockTable
                            rows={data.rows}
                            selectionMode={page === "market" ? comparisonMode : undefined}
                            columns={displayColumns}
                            widths={prefs.widths}
                            favorites={prefs.favorites}
                            selected={selected}
                            sort={query.sort}
                            direction={query.direction}
                            offset={offset}
                            onOpen={open}
                            onFavorite={favorite}
                            onSelect={select}
                            onSort={sort}
                            onMetric={onMetric}
                          />
                        </>
                      ) : (
                        <div className="mk-empty">
                          <Search size={28} />
                          <h3>
                            {page === "watchlist" && !prefs.favorites.length
                              ? "관심 있는 종목을 모아보세요"
                              : "조건에 맞는 종목이 없습니다"}
                          </h3>
                          <p>
                            {page === "watchlist"
                              ? "종목 옆 별표를 누르면 이 기기에 저장됩니다."
                              : "검색어와 조건을 바꿔 보세요."}
                          </p>
                          <button
                            className="mk-button"
                            onClick={() =>
                              page === "watchlist"
                                ? openSearch()
                                : setQuery(defaultQuery)
                            }
                          >
                            {page === "watchlist" ? "종목 검색" : "조건 초기화"}
                          </button>
                        </div>
                      )}
                      <div className="mk-table-footer">
                        <button
                          onClick={() => {
                            setRetry((n) => n + 1);
                            setRankPending(false);
                          }}
                        >
                          <RefreshCw size={14} />
                          {rankPending ? "최신 순위 반영" : "순위 새로고침"}
                        </button>
                        {page === "market" ? (
                          <button onClick={() => navigate("screener")}>
                            {data ? `${data.total.toLocaleString()}개 종목 모두 보기` : "전체 종목 보기"}
                            <ArrowRight size={15} />
                          </button>
                        ) : (
                          <div className="mk-pagination">
                            <span>
                              {data?.total
                                ? `${offset + 1}–${Math.min(offset + 40, data.total)} / ${data.total.toLocaleString()}`
                                : "0개"}
                            </span>
                            <button
                              aria-label="이전 페이지"
                              disabled={offset === 0}
                              onClick={() =>
                                setOffset((n) => Math.max(0, n - 40))
                              }
                            >
                              <ChevronLeft size={18} />
                            </button>
                            <button
                              aria-label="다음 페이지"
                              disabled={!data || offset + 40 >= data.total}
                              onClick={() => setOffset((n) => n + 40)}
                            >
                              <ChevronRight size={18} />
                            </button>
                          </div>
                        )}
                      </div>
                    </section>
                  )}
                  {page === "market" && <MarketData onSelect={open}/>}
                  {page === "market" &&
                    prefs.home
                      .filter((k) => k !== "ranking")
                      .map((section) =>
                        section === "sectors" ? (
                          <MarketExplorer key={section} overview={overview} onOpen={open} onSector={sector=>{setQuery({...defaultQuery,sector});navigate("screener");}}/>
                        ) : (
                          <section className="mk-panel" key={section}>
                            <div className="mk-section-head">
                              <div>
                                <h2>최근 공시</h2>
                              </div>
                              <a
                                href="https://dart.fss.or.kr/dsac001/mainAll.do"
                                target="_blank"
                                rel="noreferrer"
                              >
                                공시 전체 보기 ↗
                              </a>
                            </div>
                            {overview?.filings?.rows?.length ? (
                              <div className="mk-filing-list">
                                {overview.filings.rows.slice(0, 7).map((filing) => (
                                  <a
                                    className="mk-filing-row"
                                    href={filing.url}
                                    key={filing.receipt}
                                    target="_blank"
                                    rel="noreferrer"
                                    aria-label={`${filing.corp_name}, ${filing.title}, ${filing.date} 공시 원문`}
                                  >
                                    <span className="mk-filing-company">
                                      <strong>{filing.corp_name}</strong>
                                      <time>
                                        {filing.date.length === 8
                                          ? `${filing.date.slice(4, 6)}.${filing.date.slice(6, 8)}`
                                          : "날짜 미제공"}
                                      </time>
                                    </span>
                                    <span className="mk-filing-title">{filing.title}</span>
                                    <span className="mk-filing-link">보기 ↗</span>
                                  </a>
                                ))}
                              </div>
                            ) : (
                              <div className="mk-filings-empty" role="status">
                                {!overview ? "공시를 불러오는 중이에요" : overview.filings?.status === "ready"
                                  ? "최근 공시가 없습니다."
                                  : "공시를 불러오지 못했습니다. 공시 전체 보기에서 확인할 수 있습니다."}
                              </div>
                            )}
                          </section>
                        ),
                      )}
                </div>
                {page === "market" && contextSidebar}
              </div>
            )}
          </>
        )}
      </main>
      {selected.length > 0 && page !== "compare" && !code && (
        <div
          className="mk-compare-bar"
          role="region"
          aria-label="선택한 종목 비교"
        >
          <span>
            <Check size={16} />
            <b>{selected.length}</b>개 종목 선택
          </span>
          <button onClick={() => setSelected([])}>해제</button>
          <button
            className="mk-primary mk-button"
            disabled={selected.length < 2}
            onClick={() => navigate("compare")}
          >
            종목 비교
            <ArrowRight size={15} />
          </button>
        </div>
      )}
      <nav className="mk-bottom-nav" aria-label="모바일 주 메뉴">
        {sections.filter(([id]) => id !== "compare").map(([id, name, Icon]) => (
          <button
            key={id}
            aria-label={name}
                aria-current={page === id && !code ? "page" : undefined}
            onClick={() => navigate(id)}
          >
            <Icon size={21} />
            {name}
          </button>
        ))}
        <button aria-label="더보기" onClick={() => setModal("more")}><Menu size={21}/>더보기</button>
      </nav>
      {toast && (
        <div className="mk-toast" role="status">
          <Check size={17} />
          {toast}
          {canUndoFavorite && <button onClick={() => undoFavorite.current?.()}>취소</button>}
          <button aria-label="알림 닫기" onClick={() => setToast("")}>
            <X size={14} />
          </button>
        </div>
      )}
      {metric && (
        <Dialog title={labels[metric.id]} onClose={() => setMetric(null)}>
          <div className="mk-metric-definition">
            {metric.row && (
              <div className="mk-metric-value">
                <span>{metric.row.name}</span>
                <strong>{format(metric.row[metric.id], metric.id)}</strong>
                <small>
                  {fact?.period || activeMetric?.period} ·{" "}
                  {fact?.unit || activeMetric?.unit}
                </small>
                {metric.row[metric.id] == null && (
                  <p>
                    이 항목의 자료가 아직 제공되지 않았습니다. 결측값은 0으로
                    계산하지 않습니다.
                  </p>
                )}
              </div>
            )}
            <h3>쉽게 말하면</h3>
            <p>
              {activeMetric?.definition || "지표 사전을 불러오고 있습니다."}
            </p>
            <details className="mk-help-details"><summary>계산 방식과 예시 보기</summary><h3>계산 방식</h3>
            <p className="mk-formula">{activeMetric?.formula}</p>
            <h3>기준과 단위</h3>
            <p>
              {fact?.period || activeMetric?.period} ·{" "}
              {fact?.unit || activeMetric?.unit}
            </p>
            <h3>예시</h3>
            <p>{activeMetric?.example}</p>
            <h3>확인할 점</h3>
            <p>{activeMetric?.caution}</p></details>
          </div>
        </Dialog>
      )}
      {modal === "text-size" && <Dialog title="글자 크기" onClose={() => setModal(null)}>
        <div className="mk-choice-list">{([["normal","보통"],["large","크게"],["larger","더 크게"]] as const).map(([size,label]) => <button className="mk-setting-row" key={size} aria-pressed={(prefs.textSize || (prefs.large ? "large" : "normal")) === size} onClick={() => updatePrefs({textSize:size,large:size !== "normal"})}>{label}{(prefs.textSize || (prefs.large ? "large" : "normal")) === size && <Check size={20}/>}</button>)}</div>
        <p>삼성전자 · 현재가 · 외국인 순매수</p><button className="mk-button mk-primary" onClick={() => setModal(null)}>이 크기로 보기</button>
      </Dialog>}
      {modal === "ranking" && <Dialog title="순위 선택" onClose={() => setModal(null)}>
        <div className="mk-choice-list">{([['value','desc','거래대금 많은 종목'],['cap','desc','시가총액 큰 종목'],['change','desc','많이 오른 종목'],['change','asc','많이 내린 종목'],['volume','desc','거래량 많은 종목'],['dividend','desc','배당수익률 높은 종목'],['foreign_net','desc','외국인 순매수'],['institution_net','desc','기관 순매수']] as const).map(([sort,direction,label]) => <button className="mk-setting-row" key={label} onClick={() => {setQuery(q=>({...q,sort,direction}));setOffset(0);setModal(null);}}>{label}{query.sort===sort && query.direction===direction && <Check size={20}/>}</button>)}</div>
      </Dialog>}
      {modal === "search" && (
        <Dialog title="종목 검색" onClose={() => setModal(null)}>
          <label className="mk-search-field">
            <Search size={21} />
            <input
              autoFocus
              aria-label="종목명 또는 코드"
              placeholder="삼성전자, 005930…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <p className="mk-caption">
            {search.trim()
              ? "검색 결과"
              : recentSuggestions.length
                ? "최근 본 종목"
                : `${labels[query.sort]} 상위 종목`}
          </p>
          <div className="mk-search-results" aria-live="polite">
            {searchFailed && !visibleSearchRows.length ? (
              <p className="mk-empty">검색 결과를 불러오지 못했어요. <button className="mk-button" onClick={retrySearch}>다시 시도</button></p>
            ) : searchBusy && !visibleSearchRows.length ? (
              <p className="mk-empty">검색 중…</p>
            ) : visibleSearchRows.length ? (
              visibleSearchRows.map((s) => (
                <div className="mk-search-result" key={s.code}>
                  <button onClick={() => open(s.code)}>
                    <CompanyLogo url={s.logo_url} name={s.name} />
                    <span className="mk-search-company">
                      <strong>{s.name}</strong>
                      <small>{s.market} · {s.code}</small>
                    </span>
                    {s.price!=null&&<span>
                      {format(s.price)}
                      <small className={tone(s.change)}>
                        {format(s.change, "change")}
                      </small>
                    </span>}
                  </button>
                  <button
                    className={`mk-star ${prefs.favorites.includes(s.code) ? "is-saved" : ""}`}
                    aria-label={`${s.name} 관심종목 ${prefs.favorites.includes(s.code) ? "해제" : "추가"}`}
                    onClick={() => favorite(s.code)}
                  >
                    <Star size={18} />
                  </button>
                </div>
              ))
            ) : (
              <div className="mk-empty">
                {search.trim()
                  ? "검색 결과가 없습니다. 종목명 또는 코드를 다시 확인하세요."
                  : "종목명이나 6자리 코드로 찾아보세요."}
              </div>
            )}
          </div>
        </Dialog>
      )}
      {modal === "filters" && (
        <Dialog title="조건으로 종목 찾기" onClose={() => setModal(null)} wide>
          <p className="mk-muted">
            모든 기본 조건을 충족하고, OR 조건이 있으면 그중 하나 이상을
            충족하는 종목입니다.
          </p>
          {(["rules", "any_rules"] as const).map((key) => (
            <div className="mk-rule-group" key={key}>
              <h3>
                {key === "rules"
                  ? "모든 조건 충족 (AND)"
                  : "다음 중 하나 이상 (OR)"}
              </h3>
              {filterDraft[key].map((r, i) => (
                <div className="mk-rule" key={i}>
                  <select
                    aria-label={`${key} 지표 ${i + 1}`}
                    value={r.metric}
                    onChange={(e) =>
                      setFilterDraft((d) => ({
                        ...d,
                        [key]: d[key].map((x, n) =>
                          n === i
                            ? { ...x, metric: e.target.value as MetricId }
                            : x,
                        ),
                      }))
                    }
                  >
                    {Object.entries(labels).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l} ({metrics[v]?.unit || ""})
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label={`${key} 비교 ${i + 1}`}
                    value={r.operator}
                    onChange={(e) =>
                      setFilterDraft((d) => ({
                        ...d,
                        [key]: d[key].map((x, n) =>
                          n === i
                            ? {
                                ...x,
                                operator: e.target.value as Rule["operator"],
                              }
                            : x,
                        ),
                      }))
                    }
                  >
                    <option value="gte">이상 ≥</option>
                    <option value="lte">이하 ≤</option>
                    <option value="eq">같음 =</option>
                  </select>
                  <input
                    type="number"
                    aria-label={`${key} 값 ${i + 1}`}
                    value={r.value}
                    onChange={(e) =>
                      setFilterDraft((d) => ({
                        ...d,
                        [key]: d[key].map((x, n) =>
                          n === i ? { ...x, value: Number(e.target.value) } : x,
                        ),
                      }))
                    }
                  />
                  <button
                    className="mk-icon"
                    aria-label={`조건 ${i + 1} 삭제`}
                    onClick={() =>
                      setFilterDraft((d) => ({
                        ...d,
                        [key]: d[key].filter((_, n) => n !== i),
                      }))
                    }
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
              <button
                className="mk-button"
                disabled={filterDraft[key].length >= 20}
                onClick={() =>
                  setFilterDraft((d) => ({
                    ...d,
                    [key]: [
                      ...d[key],
                      { metric: "per", operator: "lte", value: 20 },
                    ],
                  }))
                }
              >
                <Plus size={15} />
                조건 추가
              </button>
            </div>
          ))}
          <p className="mk-caption">
            단위는 원·주·%·배·억원을 구분합니다. 자료가 없는 종목은 해당 조건을
            통과하지 않습니다.
          </p>
          <div className="mk-dialog-actions">
            <button
              className="mk-button"
              onClick={() =>
                setFilterDraft({ ...filterDraft, rules: [], any_rules: [] })
              }
            >
              조건 비우기
            </button>
            <button className="mk-button" onClick={() => setModal(null)}>
              취소
            </button>
            <button
              className="mk-button mk-primary"
              onClick={() => {
                setQuery(filterDraft);
                setOffset(0);
                setModal(null);
              }}
            >
              조건 적용
            </button>
          </div>
        </Dialog>
      )}
      {modal === "columns" && (
        <Dialog title="열 선택" onClose={() => setModal(null)}>
          <p className="mk-caption">
            종목명은 고정됩니다. 숫자 열은 순서와 너비를 바꿀 수 있습니다.
          </p>
          <div className="mk-column-list">
            {[
              ...columnsDraft,
              ...(Object.keys(labels) as MetricId[]).filter(
                (k) => !columnsDraft.includes(k),
              ),
            ].map((k) => {
              const i = columnsDraft.indexOf(k);
              return (
                <div key={k}>
                  <label>
                    <input
                      type="checkbox"
                      checked={i >= 0}
                      onChange={() =>
                        setColumnsDraft((c) =>
                          i >= 0 ? c.filter((x) => x !== k) : [...c, k],
                        )
                      }
                    />
                    {labels[k]}
                  </label>
                  {i >= 0 && (
                    <>
                      <input
                        type="number"
                        aria-label={`${labels[k]} 열 너비`}
                        min="90"
                        max="240"
                        step="10"
                        value={widthsDraft[k] || 116}
                        onChange={(e) =>
                          setWidthsDraft((w) => ({
                            ...w,
                            [k]: Math.min(
                              240,
                              Math.max(90, Number(e.target.value)),
                            ),
                          }))
                        }
                      />
                      <button
                        className="mk-icon"
                        disabled={i === 0}
                        aria-label={`${labels[k]} 앞으로 이동`}
                        onClick={() =>
                          setColumnsDraft((c) => {
                            const a = [...c];
                            [a[i - 1], a[i]] = [a[i], a[i - 1]];
                            return a;
                          })
                        }
                      >
                        <ArrowUp size={15} />
                      </button>
                      <button
                        className="mk-icon"
                        disabled={i === columnsDraft.length - 1}
                        aria-label={`${labels[k]} 뒤로 이동`}
                        onClick={() =>
                          setColumnsDraft((c) => {
                            const a = [...c];
                            [a[i + 1], a[i]] = [a[i], a[i + 1]];
                            return a;
                          })
                        }
                      >
                        <ArrowDown size={15} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mk-dialog-actions">
            <button className="mk-button" onClick={() => setModal(null)}>
              취소
            </button>
            <button
              className="mk-button mk-primary"
              disabled={!columnsDraft.length}
              onClick={() => {
                updatePrefs({ columns: columnsDraft, widths: widthsDraft });
                setModal(null);
              }}
            >
              열 적용
            </button>
          </div>
        </Dialog>
      )}
      {modal === "save" && (
        <Dialog title="보기 저장" onClose={() => setModal(null)}>
          <p className="mk-muted">
            검색어·조건·정렬·열 순서와 너비를 함께 저장합니다.
          </p>
          <label className="mk-field">
            보기 이름
            <input
              autoFocus
              maxLength={50}
              value={viewName}
              onChange={(e) => setViewName(e.target.value)}
              placeholder="예: 배당과 수익성"
            />
          </label>
          <div className="mk-dialog-actions">
            <button
              className="mk-button mk-primary"
              disabled={!viewName.trim() || prefs.views.length >= 30}
              onClick={() => {
                updatePrefs({
                  views: [
                    ...prefs.views,
                    {
                      id: crypto.randomUUID(),
                      name: viewName.trim(),
                      query: structuredClone(query),
                      columns: [...prefs.columns],
                      widths: { ...prefs.widths },
                    },
                  ],
                });
                setModal(null);
                notify("현재 보기를 이 기기에 저장했습니다.");
              }}
            >
              보기 저장
            </button>
          </div>
          {prefs.views.map((v) => (
            <div className="mk-saved-view" key={v.id}>
              {v.name}
              <button
                aria-label={`${v.name} 보기 삭제`}
                onClick={() =>
                  updatePrefs({
                    views: prefs.views.filter((x) => x.id !== v.id),
                  })
                }
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </Dialog>
      )}
      {(modal === "more" || modal === "settings") && (
        <Dialog title={modal === "more" ? "더보기" : "화면 설정"} onClose={() => setModal(null)}>
          {modal === "more" ? <>
            <button className="mk-setting-row" onClick={() => {setModal(null); navigate("compare");}}>종목 비교 <GitCompareArrows size={20}/></button>
            <button className="mk-setting-row" onClick={() => setModal("home")}>홈 편집 <LayoutGrid size={20}/></button>
            <button className="mk-setting-row" onClick={() => setModal("settings")}>화면·시작 설정 <Settings2 size={20}/></button>
            <PortablePreferences prefs={prefs} update={updatePrefs} notify={notify}/>
          </> : <>
          <PortablePreferences prefs={prefs} update={updatePrefs} notify={notify} />
          <label className="mk-setting-row"><span>처음 열리는 화면</span><select aria-label="처음 열리는 화면" value={prefs.startPage || "market"} onChange={e => updatePrefs({startPage: e.target.value as "market" | "watchlist"})}><option value="market">시장</option><option value="watchlist">관심종목</option></select></label>
          <p className="mk-caption"><a href="/stocks/openalgo-source.tar.gz" download>OpenAlgo 소스 · AGPL-3.0</a></p>
          <h3>화면 테마</h3>
          <div className="mk-theme-options">
            {(
              [
                ["system", "시스템", Monitor],
                ["light", "라이트", Sun],
                ["dark", "다크", Moon],
              ] as const
            ).map(([v, l, Icon]) => (
              <button
                key={v}
                aria-pressed={prefs.theme === v}
                onClick={() => updatePrefs({ theme: v })}
              >
                <Icon size={21} />
                {l}
                {prefs.theme === v && <Check size={14} />}
              </button>
            ))}
          </div>
          <button className="mk-setting-row" onClick={() => setModal("text-size")}>글자 크기 <span>{prefs.textSize === "larger" ? "더 크게" : prefs.large ? "크게" : "보통"}</span></button>
          <label className="mk-setting-row">
            <span>
              <strong>조밀한 표</strong>
              <small>마우스 환경에서 행 높이 40px</small>
            </span>
            <input
              type="checkbox"
              checked={prefs.compact}
              onChange={(e) => updatePrefs({ compact: e.target.checked })}
            />
          </label>
          </>}
        </Dialog>
      )}
      {modal === "home" && (
        <Dialog title="홈 화면 편집" onClose={() => setModal(null)}>
          <p className="mk-muted">
            표시할 영역을 선택하세요. 지수와 관심종목은 항상 표시합니다.
          </p>
          {[
            ["ranking", "종목 순위"],
            ["sectors", "시장별 탐색"],
            ["filings", "공시와 자료"],
          ].map(([k, l]) => (
            <label className="mk-setting-row" key={k}>
              <strong>{l}</strong>
              <input
                type="checkbox"
                checked={prefs.home.includes(k)}
                onChange={() =>
                  updatePrefs({
                    home: prefs.home.includes(k)
                      ? prefs.home.filter((x) => x !== k)
                      : [...prefs.home, k],
                  })
                }
              />
            </label>
          ))}
          {prefs.home
            .filter((k) => k !== "ranking")
            .map((k, i, a) => (
              <div className="mk-saved-view" key={k}>
                {k === "sectors" ? "시장별 탐색" : "공시와 자료"}
                <button
                  disabled={i === 0}
                  onClick={() => {
                    const reordered = [...a];
                    [reordered[i - 1], reordered[i]] = [
                      reordered[i],
                      reordered[i - 1],
                    ];
                    updatePrefs({
                      home: [
                        ...(prefs.home.includes("ranking") ? ["ranking"] : []),
                        ...reordered,
                      ],
                    });
                  }}
                >
                  <ArrowUp size={15} />
                </button>
              </div>
            ))}
          <div className="mk-dialog-actions">
            <button
              className="mk-button"
              onClick={() => updatePrefs({ home: defaults.home })}
            >
              기본 배치로 복원
            </button>
            <button
              className="mk-button mk-primary"
              onClick={() => setModal(null)}
            >
              편집 완료
            </button>
          </div>
        </Dialog>
      )}
      {modal === "groups" && (
        <Dialog title="관심종목 그룹" onClose={() => setModal(null)}>
          <div className="mk-toolbar">
            <input
              aria-label="새 그룹 이름"
              placeholder="새 그룹 이름"
              maxLength={30}
              value={newGroup}
              onChange={(e) => setNewGroup(e.target.value)}
            />
            <button
              className="mk-button"
              disabled={
                !newGroup.trim() ||
                newGroup === "전체" ||
                !!prefs.groups[newGroup.trim()]
              }
              onClick={() => {
                updatePrefs({
                  groups: { ...prefs.groups, [newGroup.trim()]: [] },
                });
                setNewGroup("");
              }}
            >
              그룹 만들기
            </button>
          </div>
          {Object.entries(prefs.groups).map(([name, codes]) => (
            <div className="mk-rule-group" key={name}>
              <div className="mk-section-head">
                <h3>{name}</h3>
                <button
                  aria-label={`${name} 그룹 삭제`}
                  onClick={() => {
                    updatePrefs({
                      groups: Object.fromEntries(
                        Object.entries(prefs.groups).filter(
                          ([n]) => n !== name,
                        ),
                      ),
                    });
                    if (group === name) setGroup("전체");
                  }}
                >
                  <Trash2 size={15} />
                </button>
              </div>
              {savedRows
                .filter((s) => prefs.favorites.includes(s.code))
                .map((s) => (
                  <label className="mk-check" key={s.code}>
                    <input
                      type="checkbox"
                      checked={codes.includes(s.code)}
                      onChange={() =>
                        updatePrefs({
                          groups: {
                            ...prefs.groups,
                            [name]: codes.includes(s.code)
                              ? codes.filter((c) => c !== s.code)
                              : [...codes, s.code],
                          },
                        })
                      }
                    />
                    {s.name}
                  </label>
                ))}
            </div>
          ))}
        </Dialog>
      )}
    </div>
  );
}
