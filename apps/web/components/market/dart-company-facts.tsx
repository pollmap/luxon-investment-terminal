import type { ReactNode } from "react";
import type { Detail } from "./types";

type Facts = NonNullable<NonNullable<Detail["official"]>["report_facts"]>;
type Row = Record<string, string | number | null>;

function value(row: Row, key: string) {
  const item = row[key];
  return item === null || item === undefined || item === "" ? null : item;
}

function text(row: Row, key: string) {
  const item = value(row, key);
  return item === null ? "—" : String(item);
}

function quantity(row: Row, key: string, suffix = "") {
  const item = value(row, key);
  if (typeof item !== "number" || !Number.isFinite(item)) return "—";
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(item)}${suffix}`;
}

function rowsFor(section: Facts["sections"][keyof Facts["sections"]]) {
  return section?.status === "ready" && Array.isArray(section.rows) ? section.rows : [];
}

function DataTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: ReactNode[][];
}) {
  return (
    <div className="mk-table-scroll">
      <table className="mk-data-table mk-company-fact-table">
        <thead>
          <tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DartCompanyFacts({ facts, only }: { facts?: Facts; only?: "dividends" | "shareholders" | "overview" }) {
  if (!facts) return null;
  const allowed = only === "dividends" ? ["dividends"] : only === "shareholders"
    ? ["shareholders", "shares", "minority_shareholders", "holder_changes", "treasury"]
    : only === "overview" ? ["employees", "executives", "auditor", "investments"] : null;
  const sections: Facts["sections"] = Object.fromEntries(Object.entries(facts.sections || {}).filter(([key]) => !allowed || allowed.includes(key)));
  const shareholderRows = rowsFor(sections.shareholders);
  const dividendRows = rowsFor(sections.dividends);
  const shareRows = rowsFor(sections.shares);
  const employeeRows = rowsFor(sections.employees);
  const executiveRows = rowsFor(sections.executives);
  const minorityRows = rowsFor(sections.minority_shareholders);
  const holderChangeRows = rowsFor(sections.holder_changes);
  const auditorRows = rowsFor(sections.auditor);
  const treasuryRows = rowsFor(sections.treasury);
  const investmentRows = rowsFor(sections.investments);
  if (!shareholderRows.length && !dividendRows.length && !shareRows.length && !employeeRows.length && !executiveRows.length && !minorityRows.length && !holderChangeRows.length && !auditorRows.length && !treasuryRows.length && !investmentRows.length) {
    return null;
  }

  return (
    <section className="mk-company-report-facts" aria-label="회사 공시 정보">
      <div className="mk-section-head">
        <h2>{only === "dividends" ? "배당 내역" : only === "shareholders" ? "주주와 주식" : "회사 공시 정보"}</h2>
      </div>

      {!!shareRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>발행 주식 <span>{sections.shares?.year}년</span></summary>
          <div className="mk-company-share-grid">
            {shareRows.map((row, index) => (
              <article key={`${text(row, "stock_type")}-${index}`}>
                <span>{text(row, "stock_type")}</span>
                <dl>
                  <div><dt>발행</dt><dd>{quantity(row, "issued")}주</dd></div>
                  <div><dt>자기주식</dt><dd>{quantity(row, "treasury")}주</dd></div>
                  <div><dt>유통</dt><dd>{quantity(row, "circulating")}주</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </details>
      )}

      {!!shareholderRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>최대주주와 특수관계인 <span>{sections.shareholders?.year}년</span></summary>
          <DataTable
            headers={["주주", "관계", "주식 종류", "기초 보유", "기말 보유", "기말 지분율"]}
            rows={shareholderRows.map((row) => [
              text(row, "name"),
              text(row, "relationship"),
              text(row, "stock_type"),
              `${quantity(row, "beginning_shares")}주`,
              `${quantity(row, "ending_shares")}주`,
              quantity(row, "ending_ratio", "%"),
            ])}
          />
        </details>
      )}

      {!!dividendRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>배당 내역 <span>{sections.dividends?.year}년 · {dividendRows.length}개 항목</span></summary>
          <DataTable
            headers={["항목", "주식 종류", "당기", "전기", "전전기"]}
            rows={dividendRows.map((row) => [
              text(row, "category"),
              text(row, "stock_type"),
              quantity(row, "current"),
              quantity(row, "previous"),
              quantity(row, "prior"),
            ])}
          />
        </details>
      )}

      {!!employeeRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>직원 현황 <span>{sections.employees?.year}년 · {employeeRows.length}개 항목</span></summary>
          <DataTable
            headers={["사업 부문", "성별", "정규직", "계약직", "합계", "평균 근속연수"]}
            rows={employeeRows.map((row) => [
              text(row, "division"),
              text(row, "gender"),
              quantity(row, "regular"),
              quantity(row, "contract"),
              quantity(row, "total"),
              quantity(row, "average_tenure", "년"),
            ])}
          />
        </details>
      )}

      {!!executiveRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>임원 현황 <span>{sections.executives?.year}년 · {executiveRows.length}명</span></summary>
          <DataTable
            headers={["이름", "직위", "구분", "담당 업무", "임기 만료"]}
            rows={executiveRows.map((row) => [
              text(row, "name"),
              text(row, "position"),
              [text(row, "registered"), text(row, "full_time")].filter((item) => item !== "—").join(" · ") || "—",
              text(row, "responsibility"),
              text(row, "term_end"),
            ])}
          />
        </details>
      )}

      {!!minorityRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>소액주주 현황 <span>{sections.minority_shareholders?.year}년 · {quantity(minorityRows[0], "holders")}명 · 지분 {quantity(minorityRows[0], "share_ratio", "%")}</span></summary>
          <DataTable
            headers={["구분", "주주 수", "전체 주주 중", "보유 주식", "발행 주식 중"]}
            rows={minorityRows.map((row) => [
              text(row, "category"),
              `${quantity(row, "holders")}명 / ${quantity(row, "total_holders")}명`,
              quantity(row, "holder_ratio", "%"),
              `${quantity(row, "shares")}주 / ${quantity(row, "total_shares")}주`,
              quantity(row, "share_ratio", "%"),
            ])}
          />
        </details>
      )}

      {!!holderChangeRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>최대주주 변동 <span>{sections.holder_changes?.year}년 · {holderChangeRows.length}건</span></summary>
          <DataTable
            headers={["변동일", "최대주주", "보유 주식", "지분율", "변동 사유"]}
            rows={holderChangeRows.map((row) => [
              text(row, "date"),
              text(row, "name"),
              `${quantity(row, "shares")}주`,
              quantity(row, "ratio", "%"),
              text(row, "cause"),
            ])}
          />
        </details>
      )}

      {!!auditorRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>회계감사 의견 <span>{sections.auditor?.year}년 · {text(auditorRows[0], "opinion")}</span></summary>
          <DataTable
            headers={["감사인", "감사의견", "강조사항", "핵심감사사항"]}
            rows={auditorRows.map((row) => [
              text(row, "auditor"),
              text(row, "opinion"),
              text(row, "emphasis") !== "—" ? text(row, "emphasis") : text(row, "special_matter"),
              text(row, "key_audit_matters"),
            ])}
          />
        </details>
      )}

      {!!treasuryRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>자기주식 내역 <span>{sections.treasury?.year}년 · 기말 {quantity(treasuryRows.find((row) => value(row, "ending") !== null) || treasuryRows[0], "ending")}주</span></summary>
          <DataTable
            headers={["취득 구분", "주식 종류", "기초", "취득", "처분", "소각", "기말"]}
            rows={treasuryRows.map((row) => [
              [text(row, "method"), text(row, "method_detail"), text(row, "method_type")].filter((item) => item !== "—").join(" · ") || "—",
              text(row, "stock_type"),
              `${quantity(row, "beginning")}주`,
              `${quantity(row, "acquired")}주`,
              `${quantity(row, "disposed")}주`,
              `${quantity(row, "cancelled")}주`,
              `${quantity(row, "ending")}주`,
            ])}
          />
        </details>
      )}

      {!!investmentRows.length && (
        <details className="mk-company-fact-disclosure">
          <summary>타법인 출자 <span>{sections.investments?.year}년 · {investmentRows.length}개 법인</span></summary>
          <DataTable
            headers={["법인", "출자 목적", "기말 보유 수량", "지분율", "장부가액", "당기 순이익"]}
            rows={investmentRows.map((row) => [
              text(row, "company"),
              text(row, "purpose"),
              `${quantity(row, "ending_shares")}주`,
              quantity(row, "ending_ratio", "%"),
              quantity(row, "ending_value"),
              quantity(row, "net_income"),
            ])}
          />
        </details>
      )}
    </section>
  );
}
