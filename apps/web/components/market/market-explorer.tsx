"use client";

import { useEffect, useId, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import Dialog from "./dialog";
import CompanyLogo from "./company-logo";
import TossSectors from "./toss-sectors";
import { useExplorationState } from "./exploration-state";
import { format, request, tone, type Overview, type Stock } from "./types";

type Theme = { id: number; name: string; count: number; change: number };
type Members = { rows: Stock[]; name: string; description?: string };
type Sector = { name: string; count: number; change: number | null; value: number };
const tabs = ["업종", "산업", "테마", "종목 지도"] as const;
type Exploration = { tab: (typeof tabs)[number]; query: string; limit: number; sectorQuery: string; sectorLimit: number; sectorSort: string };
const initialExploration: Exploration = {tab:"업종",query:"",limit:12,sectorQuery:"",sectorLimit:8,sectorSort:"value"};
function isExploration(value: unknown): value is Exploration {
  if (!value || typeof value !== "object") return false;
  const row = value as Exploration;
  return tabs.includes(row.tab) && typeof row.query === "string" && row.query.length <= 200 && typeof row.sectorQuery === "string" && row.sectorQuery.length <= 200 && ["value","change","name"].includes(row.sectorSort) && [row.limit,row.sectorLimit].every(n => Number.isInteger(n) && n >= 8 && n <= 10000);
}

export default function MarketExplorer({
  overview,
  onSector,
  onOpen,
}: {
  overview: Overview | null;
  onSector: (sector: string) => void;
  onOpen: (code: string) => void;
}) {
  const [exploration, setExploration, restored] = useExplorationState("korea-stocks:exploration:v1", initialExploration, isExploration);
  const {tab, query, limit, sectorQuery, sectorLimit, sectorSort} = exploration;
  const setTab = (tab: Exploration["tab"]) => setExploration(previous => ({...previous,tab}));
  const setQuery = (query: string) => setExploration(previous => ({...previous,query,limit:12}));
  const setLimit = (next: number | ((n:number)=>number)) => setExploration(previous => ({...previous,limit: typeof next === "function" ? next(previous.limit) : next}));
  const setSectorQuery = (sectorQuery: string) => setExploration(previous => ({...previous,sectorQuery,sectorLimit:8}));
  const setSectorLimit = (next: number | ((n:number)=>number)) => setExploration(previous => ({...previous,sectorLimit: typeof next === "function" ? next(previous.sectorLimit) : next}));
  const setSectorSort = (sectorSort: string) => setExploration(previous => ({...previous,sectorSort}));
  const tabId = useId();
  const [themes, setThemes] = useState<Theme[]>([]);
  const [selected, setSelected] = useState<Theme | null>(null);
  const [members, setMembers] = useState<Members | null>(null);
  const [stocks, setStocks] = useState<Stock[]>([]);
  const [error, setError] = useState("");
  const [themeLoading, setThemeLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [sectorError, setSectorError] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    if (!restored || tab !== "업종") return;
    setSectorError(false);
    request<{rows: Sector[]}>("/sectors", {signal: abort.signal})
      .then(result => { if (!abort.signal.aborted) setSectors(result.rows); })
      .catch(() => { if (!abort.signal.aborted) setSectorError(true); });
    return () => abort.abort();
  }, [tab, retry, restored]);
  const visibleSectors = [...(sectors.length ? sectors : overview?.sectors || [])]
    .filter(sector => sector.name.includes(sectorQuery.trim()))
    .sort((a, b) => sectorSort === "name" ? a.name.localeCompare(b.name, "ko") :
      sectorSort === "change" ? (b.change ?? -Infinity) - (a.change ?? -Infinity) : b.value - a.value);

  useEffect(() => {
    if (!restored || tab !== "테마") return;
    let active = true;
    setThemeLoading(true);
    setError("");
    request<{ rows: Theme[] }>("/themes")
      .then((result) => {
        if (!active) return;
        setThemes(result.rows || []);
        if (!result.rows?.length) setError("테마를 불러오지 못했습니다.");
      })
      .catch(() => {
        if (active) setError("테마를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setThemeLoading(false);
      });
    return () => {
      active = false;
    };
  }, [tab, retry, restored]);

  useEffect(() => {
    if (!restored || tab !== "종목 지도") return;
    let active = true;
    request<{ rows: Stock[] }>("/screener", {
      method: "POST",
      body: JSON.stringify({ sort: "value", limit: 24 }),
    })
      .then((result) => {
        if (active) setStocks(result.rows);
      })
      .catch(() => {
        if (active) setStocks([]);
      });
    return () => {
      active = false;
    };
  }, [tab, restored]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setMembers(null);
    request<Members>(`/themes/${selected.id}`)
      .then((result) => {
        if (active) setMembers(result);
      })
      .catch(() => {
        if (active) setMembers({ rows: [], name: selected.name });
      });
    return () => {
      active = false;
    };
  }, [selected]);

  const visibleThemes = themes.filter((theme) =>
    theme.name.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  return (
    <section className="mk-panel mk-explorer" aria-busy={!restored}>
      <fieldset className="mk-exploration-inputs" disabled={!restored} aria-label="시장 탐색 조작">
      <div className="mk-section-head">
        <div>
          <h2>업종·테마별 종목 찾기</h2>
        </div>
        <div className="mk-segment" role="tablist" aria-label="시장 탐색">
          {tabs.map((item) => (
            <button
              key={item}
              role="tab"
              id={`${tabId}-${item}`}
              aria-controls={`${tabId}-panel`}
              aria-selected={tab === item}
              tabIndex={tab === item ? 0 : -1}
              onKeyDown={event => {
                const index = tabs.indexOf(item);
                const next = event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length] : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length] : event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : null;
                if (next) { event.preventDefault(); setTab(next); document.getElementById(`${tabId}-${next}`)?.focus(); }
              }}
              onClick={() => setTab(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${tab}`}>
      {tab === "산업" && <TossSectors onOpen={onOpen} />}
      {tab === "업종" && (
        <>
          <div className="mk-sector-controls">
            <label className="mk-theme-search"><Search size={17} /><input aria-label="업종 검색" placeholder="업종 검색" value={sectorQuery} onChange={event => {setSectorQuery(event.target.value); setSectorLimit(8);}} /></label>
            <select aria-label="업종 정렬" value={sectorSort} onChange={event => setSectorSort(event.target.value)}><option value="value">거래대금순</option><option value="change">상승률순</option><option value="name">가나다순</option></select>
          </div>
          <div className="mk-sector-bars">
            {visibleSectors.slice(0, sectorLimit).map((sector) => (
              <button key={sector.name} onClick={() => onSector(sector.name)}>
                <span>
                  {sector.name}
                  <small>{sector.count}종목</small>
                </span>
                <span className="mk-sector-meter">
                  <i
                    style={{
                      width: `${sector.change === null ? 0 : Math.min(100, Math.max(3, Math.abs(sector.change) * 12))}%`,
                      background:
                        (sector.change ?? 0) >= 0 ? "var(--mk-up)" : "var(--mk-down)",
                    }}
                  />
                </span>
                <b className={tone(sector.change)}>
                  {format(sector.change, "change")}
                </b>
                <ArrowUpRight size={15} />
              </button>
            ))}
          </div>
          {!visibleSectors.length && <p role="status" className="mk-muted">{sectorError ? "업종을 불러오지 못했습니다." : sectorQuery ? "일치하는 업종이 없습니다." : "업종을 불러오는 중…"}</p>}
          {sectorError && <button className="mk-button" onClick={() => setRetry(v => v + 1)}>다시 불러오기</button>}
          {visibleSectors.length > sectorLimit && <button className="mk-button" onClick={() => setSectorLimit(n => n + 16)}>업종 더 보기 ({visibleSectors.length})</button>}
          <p className="mk-caption">
            업종별 등락률은 포함 종목의 단순 평균입니다.
          </p>
        </>
      )}

      {tab === "테마" && (
        <>
          <label className="mk-theme-search">
            <Search size={17} />
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setLimit(12);
              }}
              placeholder="반도체, 로봇, 배당…"
              aria-label="테마 검색"
            />
          </label>
          <div className="mk-theme-grid">
            {visibleThemes.slice(0, limit).map((theme) => (
              <button key={theme.id} onClick={() => setSelected(theme)}>
                <span>{theme.name}</span>
                <small>{theme.count}종목</small>
                <strong className={tone(theme.change)}>
                  {format(theme.change, "change")}
                </strong>
              </button>
            ))}
          </div>
          {!visibleThemes.length && (
            <p className="mk-muted">
              {themeLoading
                ? "테마를 불러오는 중…"
                : error || "일치하는 테마가 없습니다."}
            </p>
          )}
          {error && (
            <button className="mk-button" onClick={() => setRetry((value) => value + 1)}>
              다시 불러오기
            </button>
          )}
          {visibleThemes.length > limit && (
            <button className="mk-button" onClick={() => setLimit((value) => value + 12)}>
              테마 더 보기 ({visibleThemes.length})
            </button>
          )}
          <p className="mk-caption">
            한 기업이 여러 테마에 포함될 수 있어 업종 분류와 다를 수 있습니다.
          </p>
        </>
      )}

      {tab === "종목 지도" && (
        <>
          <div className="mk-heatmap">
            {stocks.map((stock) => (
              <button
                key={stock.code}
                onClick={() => onOpen(stock.code)}
                style={{
                  background: `color-mix(in srgb, ${stock.change! >= 0 ? "var(--mk-up)" : "var(--mk-down)"} ${Math.min(28, 6 + Math.abs(stock.change || 0) * 2)}%, var(--mk-surface))`,
                }}
              >
                <CompanyLogo url={stock.logo_url} name={stock.name} />
                <strong>{stock.name}</strong>
                <b className={tone(stock.change)}>
                  {format(stock.change, "change")}
                </b>
              </button>
            ))}
          </div>
          <p className="mk-caption">
            거래대금 상위 24종목 · 같은 크기 · 색상은 당일 등락률
          </p>
        </>
      )}

      </div>
      </fieldset>
      {selected && (
        <Dialog
          title={selected.name}
          onClose={() => setSelected(null)}
          wide
        >
          <div className="mk-theme-intro">
            <strong className={tone(selected.change)}>
              {format(selected.change, "change")}
            </strong>
            <span>{selected.count}개 편입 종목</span>
          </div>
          {members?.description && <p className="mk-muted">{members.description}</p>}
          <div className="mk-member-list">
            {members ? (
              members.rows.map((stock) => (
                <button
                  key={stock.code}
                  onClick={() => {
                    setSelected(null);
                    onOpen(stock.code);
                  }}
                >
                  <CompanyLogo url={stock.logo_url} name={stock.name} />
                  <span>
                    <strong>{stock.name}</strong>
                    <small>{stock.code} · {stock.market}</small>
                  </span>
                  <b className={tone(stock.change)}>
                    {format(stock.change, "change")}
                  </b>
                </button>
              ))
            ) : (
              <p role="status">종목을 불러오는 중…</p>
            )}
          </div>
          {members && !members.rows.length && (
            <p>포함된 종목이 없습니다.</p>
          )}
        </Dialog>
      )}
    </section>
  );
}
