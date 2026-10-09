"use client";
import {useState} from "react";
import CompanyLogo from "./company-logo";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Star,
  PanelRightOpen,
  Info,
} from "lucide-react";
import {
  format,
  labels,
  tone,
  type MetricId,
  type Stock,
} from "./types";

type Props = {
  rows: Stock[];
  columns: MetricId[];
  widths: Record<string, number>;
  favorites: string[];
  selected: string[];
  sort: string;
  direction: string;
  offset?: number;
  selectionMode?: boolean;
  onOpen: (code: string) => void;
  onFavorite: (code: string) => void;
  onSelect: (code: string) => void;
  onPreview: (code: string) => void;
  onSort: (key: MetricId) => void;
  onMetric: (key: MetricId, row?: Stock) => void;
};
export default function StockTable(p: Props) {
  const [selecting,setSelecting]=useState(false);
  const showSelection=(p.selectionMode ?? selecting)||p.selected.length>0;
  return (
    <div className="mk-stock-table-wrap">
      {p.selectionMode === undefined && <div className="mk-table-actions"><button className="mk-button" aria-pressed={showSelection} onClick={()=>{if(p.selected.length){p.selected.forEach(p.onSelect);}setSelecting(!showSelection);}}>{showSelection?"비교 선택 닫기":"비교할 종목 선택"}</button></div>}
    <div className="mk-table-scroll">
      <table className={`mk-stock-table${showSelection ? " mk-selecting" : ""}`}>
        <caption className="mk-sr">
          종목 목록. 종목명으로 상세를 열고 체크박스로 비교할 종목을 선택하세요.
        </caption>
        <thead>
          <tr>
            {showSelection&&<th className="mk-select-cell">
              <span className="mk-sr">비교</span>
            </th>}
            <th className="mk-name-cell">종목명</th>
            {p.columns.map((k) => (
              <th
                key={k}
                scope="col"
                aria-sort={
                  p.sort === k
                    ? p.direction === "desc"
                      ? "descending"
                      : "ascending"
                    : "none"
                }
                style={{ minWidth: p.widths[k] || 116 }}
              >
                <div className="mk-th">
                  <button
                    onClick={() => p.onSort(k)}
                    aria-label={`${labels[k]} 정렬`}
                  >
                    {labels[k]}
                    {p.sort === k ? (
                      p.direction === "desc" ? (
                        <ArrowDown size={13} />
                      ) : (
                        <ArrowUp size={13} />
                      )
                    ) : (
                      <ArrowUpDown size={12} />
                    )}
                  </button>
                  <button
                    className="mk-info"
                    aria-label={`${labels[k]} 설명`}
                    onClick={() => p.onMetric(k)}
                  >
                    <Info size={13} />
                  </button>
                </div>
              </th>
            ))}
            <th className="mk-preview-cell">
              <span className="mk-sr">빠른 확인</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {p.rows.map((row, index) => (
            <tr key={row.code} data-selected={p.selected.includes(row.code)}>
              {showSelection&&<td className="mk-select-cell">
                <input
                  type="checkbox"
                  aria-label={`${row.name} 비교 선택`}
                  checked={p.selected.includes(row.code)}
                  disabled={
                    p.selected.length >= 4 && !p.selected.includes(row.code)
                  }
                  onChange={() => p.onSelect(row.code)}
                />
              </td>}
              <td className="mk-name-cell">
                <div className="mk-stock-name">
                  <button
                    className={`mk-star ${p.favorites.includes(row.code) ? "is-saved" : ""}`}
                    aria-label={`${row.name} 관심종목 ${p.favorites.includes(row.code) ? "해제" : "추가"}`}
                    aria-pressed={p.favorites.includes(row.code)}
                    onClick={() => p.onFavorite(row.code)}
                  >
                    <Star size={17} />
                  </button>
                  <span className="mk-rank">{(p.offset || 0) + index + 1}</span>
                  <CompanyLogo url={row.logo_url} name={row.name}/>
                  <button
                    data-code={row.code}
                    className="mk-stock-link"
                    onClick={() => p.onOpen(row.code)}
                  >
                    <strong>{row.name}</strong>
                    <small>
                      {row.code}
                      <span>{row.market}</span>
                    </small>
                  </button>
                </div>
              </td>
              {p.columns.map((k) => (
                <td
                  key={k}
                  className={
                    ["change", "foreign_net", "institution_net"].includes(k)
                      ? tone(row[k])
                      : ""
                  }
                >
                  <span
                    className="mk-value"
                    aria-label={`${row.name} ${labels[k]} ${format(row[k], k)}`}
                  >
                    {format(row[k], k)}
                  </span>
                </td>
              ))}
              <td className="mk-preview-cell">
                <button
                  className="mk-icon"
                  aria-label={`${row.name} 빠른 확인`}
                  onClick={() => p.onPreview(row.code)}
                >
                  <PanelRightOpen size={16} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    </div>
  );
}
