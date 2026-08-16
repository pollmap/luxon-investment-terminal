"use client";

import { Search, X } from "lucide-react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

export type SearchSecurityOption = {
  ticker: string;
  label: string;
  market: string;
  currency: string;
};

export type SearchWorkspaceOption = {
  key: string;
  label: string;
  detail: string;
};

type SearchFilter = "all" | "securities" | "portfolios" | "screens" | "source_traces";

type SearchResult =
  | {
      kind: "security";
      group: "securities";
      ticker: string;
      label: string;
      detail: string;
      searchText: string;
    }
  | {
      kind: "workspace";
      group: Exclude<SearchFilter, "all" | "securities" | "source_traces">;
      key: string;
      label: string;
      detail: string;
      searchText: string;
    }
  | {
      kind: "source_trace";
      group: "source_traces";
      id: string;
      key: string;
      label: string;
      detail: string;
      searchText: string;
    };

type SearchOverlayProps = {
  selectedTicker: string;
  securities: SearchSecurityOption[];
  workspaces: SearchWorkspaceOption[];
  onSelectTicker: (ticker: string) => void;
  onSelectWorkspace: (workspace: string) => void;
};

const filters: { value: SearchFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "securities", label: "Securities" },
  { value: "portfolios", label: "Portfolios" },
  { value: "screens", label: "Screens" },
  { value: "source_traces", label: "Source traces" }
];

const sourceTraceCommands: SearchResult[] = [
  {
    kind: "source_trace",
    group: "source_traces",
    id: "source-trace-ledger",
    key: "Data Audit",
    label: "Source Trace Ledger",
    detail: "Open filing id, source document, formula, method, confidence, and flags",
    searchText: "source trace ledger filing source document formula method confidence flags audit data audit"
  },
  {
    kind: "source_trace",
    group: "source_traces",
    id: "adjusted-eps-waterfall",
    key: "Data Audit",
    label: "Adjusted EPS Waterfall",
    detail: "Inspect GAAP to adjusted EPS bridge, tax effects, S1/S2/S4 method, and warnings",
    searchText: "adjusted eps waterfall gaap bridge tax effect s1 s2 s4 warning source trace"
  },
  {
    kind: "source_trace",
    group: "source_traces",
    id: "forecast-source-trace",
    key: "Forecasting",
    label: "Forecast Source Trace",
    detail: "Review 1Y-5Y consensus, user input, deterministic formula, and AI commentary guard",
    searchText: "forecast source trace 1y 5y consensus user input deterministic formula ai commentary guard"
  }
];

const sourceRoutes = [
  {
    label: "KR priority",
    value: "source required",
    detail: "OpenDART + pykrx rows must pass source_trace before display."
  },
  {
    label: "US / JP",
    value: "staged",
    detail: "EDGAR, EDINET, and J-Quants connectors stay behind the same gate."
  },
  {
    label: "Data Audit",
    value: "click-through",
    detail: "Every visible financial number must resolve to a source trace."
  }
];

export function SearchOverlay({
  selectedTicker,
  securities,
  workspaces,
  onSelectTicker,
  onSelectWorkspace
}: SearchOverlayProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SearchFilter>("all");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const selectedSecurity =
    securities.find((security) => security.ticker === selectedTicker) ?? securities[0];

  const results = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const securityResults: SearchResult[] = securities.map((security) => ({
      kind: "security",
      group: "securities",
      ticker: security.ticker,
      label: `${security.ticker}  ${security.label}`,
      detail: `${security.market} Equity - ${security.currency}`,
      searchText: `${security.ticker} ${security.label} ${security.market} ${security.currency}`.toLowerCase()
    }));
    const workspaceResults: SearchResult[] = workspaces.map((workspace) => ({
      kind: "workspace",
      group: workspaceGroup(workspace.key),
      key: workspace.key,
      label: workspace.label,
      detail: workspace.detail,
      searchText: `${workspace.key} ${workspace.label} ${workspace.detail}`.toLowerCase()
    }));
    return [...securityResults, ...workspaceResults, ...sourceTraceCommands]
      .filter((result) => filter === "all" || result.group === filter)
      .filter((result) => !normalizedQuery || result.searchText.includes(normalizedQuery));
  }, [filter, query, securities, workspaces]);

  useEffect(() => {
    function openFromSlash(event: KeyboardEvent) {
      if (event.key !== "/" || open || isTextEntryTarget(event.target)) {
        return;
      }
      event.preventDefault();
      setOpen(true);
    }
    window.addEventListener("keydown", openFromSlash);
    return () => window.removeEventListener("keydown", openFromSlash);
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setQuery(selectedTicker);
    setActiveIndex(0);
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open, selectedTicker]);

  useEffect(() => {
    setActiveIndex(0);
  }, [filter, query]);

  function closeOverlay() {
    setOpen(false);
    setQuery("");
    setFilter("all");
  }

  function selectResult(result: SearchResult | undefined) {
    if (!result) {
      return;
    }
    if (result.kind === "security") {
      onSelectTicker(result.ticker);
    } else {
      onSelectWorkspace(result.key);
    }
    closeOverlay();
  }

  function handleOverlayKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeOverlay();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(results.length - 1, index + 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(0, index - 1));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      selectResult(results[activeIndex]);
    }
  }

  return (
    <>
      <button
        className="search-box search-box-button"
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open global search"
        data-testid="global-search-trigger"
      >
        <Search size={16} />
        <span className="search-command-text">/ Search securities, portfolios, screens, source traces</span>
        <span className="search-selected-chip" data-testid="search-selected-ticker">
          {selectedSecurity ? `${selectedSecurity.ticker} - ${selectedSecurity.label}` : selectedTicker}
        </span>
        <kbd>/</kbd>
      </button>
      {open ? (
        <div
          className="search-overlay-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Global search overlay"
          data-testid="global-search-overlay"
          onKeyDown={handleOverlayKeyDown}
        >
          <div className="search-overlay-panel">
            <aside className="search-overlay-rail" aria-label="Search filters">
              <div className="search-overlay-rail-title">Filters</div>
              {filters.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={filter === item.value ? "active" : ""}
                  onClick={() => setFilter(item.value)}
                >
                  {item.label}
                </button>
              ))}
            </aside>
            <section className="search-overlay-main">
              <div className="search-overlay-heading">
                <strong>Command Search</strong>
                <span>/ Search securities, portfolios, screens, source traces</span>
              </div>
              <div className="search-overlay-input-row">
                <Search size={17} />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="Global search query"
                  data-testid="global-search-input"
                  placeholder="Search securities, portfolios, screens, source traces"
                />
                <button type="button" aria-label="Close global search" onClick={closeOverlay}>
                  <X size={18} />
                </button>
              </div>
              <div className="search-result-list" role="listbox" aria-label="Global search results">
                {results.length ? (
                  results.map((result, index) => (
                    <button
                      key={searchResultKey(result)}
                      type="button"
                      role="option"
                      aria-selected={index === activeIndex}
                      className={index === activeIndex ? "active" : ""}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => selectResult(result)}
                      data-testid={searchResultTestId(result)}
                    >
                      <strong>{result.label}</strong>
                      <span>{result.detail}</span>
                    </button>
                  ))
                ) : (
                  <div className="search-empty-state">
                    <strong>No source-tracked result</strong>
                    <span>Try a ticker, workspace, portfolio, screen, or source trace.</span>
                  </div>
                )}
              </div>
              <div className="search-shortcuts" aria-label="Search keyboard shortcuts">
                <span>Up/Down navigate</span>
                <span>Enter select</span>
                <span>Esc close</span>
                <span>/ search anytime</span>
              </div>
            </section>
            <aside
              className="search-overlay-source-routing"
              aria-label="Source routing"
              data-testid="search-overlay-source-routing"
            >
              <div>
                <span>Source Routing</span>
                <strong>No source_trace, no number</strong>
              </div>
              <p>
                Search opens the workspace immediately. Financial values stay locked until
                source, filing, period, unit, currency, method, formula, confidence, and flags
                are available.
              </p>
              <div className="search-overlay-route-stack">
                {sourceRoutes.map((route) => (
                  <div className="search-overlay-route-card" key={route.label}>
                    <span>{route.label}</span>
                    <strong data-testid={`search-overlay-route-${route.label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
                      {route.value}
                    </strong>
                    <p>{route.detail}</p>
                  </div>
                ))}
              </div>
              <button
                type="button"
                onClick={() =>
                  selectResult(
                    sourceTraceCommands.find(
                      (command) => command.kind === "source_trace" && command.id === "source-trace-ledger"
                    )
                  )
                }
              >
                Open Data Audit
              </button>
            </aside>
          </div>
        </div>
      ) : null}
    </>
  );
}

function workspaceGroup(key: string): Exclude<SearchFilter, "all" | "securities" | "source_traces"> {
  if (key === "Portfolio" || key === "Watchlist") {
    return "portfolios";
  }
  return "screens";
}

function searchResultTestId(result: SearchResult) {
  if (result.kind === "security") {
    return `search-result-${result.ticker}`;
  }
  if (result.kind === "source_trace") {
    return `search-result-${result.id}`;
  }
  return `search-result-${result.key}`;
}

function searchResultKey(result: SearchResult) {
  if (result.kind === "security") {
    return `security-${result.ticker}`;
  }
  if (result.kind === "source_trace") {
    return `source-trace-${result.id}`;
  }
  return `workspace-${result.key}`;
}

function isTextEntryTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}
