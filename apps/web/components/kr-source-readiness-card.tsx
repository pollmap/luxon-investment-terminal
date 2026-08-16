"use client";

import { auditFactHref, auditTestIdPart, sourceDocumentHref } from "../lib/audit-utils";
import type {
  KrValuationCacheCoverage,
  KrValuationCacheGapAuditRef,
  KrValuationCacheUniverseCoverage
} from "../lib/terminal-types";

type KrSourceReadinessCardProps = {
  title: string;
  testIdPrefix: string;
  ticker?: string | null;
  krCacheCoverage?: KrValuationCacheCoverage | null;
  krCacheUniverse?: KrValuationCacheUniverseCoverage | null;
};

export function KrSourceReadinessCard({
  title,
  testIdPrefix,
  ticker,
  krCacheCoverage,
  krCacheUniverse
}: KrSourceReadinessCardProps) {
  if (!krCacheCoverage && !krCacheUniverse) {
    return null;
  }

  const selectedStatus = formatStatus(krCacheCoverage?.coverage_status ?? "not loaded");
  const selectedBackend = formatStatus(krCacheCoverage?.data_backend ?? "not loaded");
  const selectedYears = formatYearList(krCacheCoverage?.coverage_years?.valuation_points ?? []);
  const selectedNumbers = krCacheCoverage?.financial_numbers_allowed ? "allowed" : "blocked";
  const selectedMissing = formatMissingYears(krCacheCoverage);
  const summary = krCacheUniverse?.summary;
  const universeReady = summary ? `${summary.valuation_ready}/${summary.tickers_expected}` : "-";
  const universeMix = summary
    ? `${summary.complete} complete / ${summary.partial_source_backed} partial / ${summary.missing} missing`
    : "-";
  const sourceDocumentId = traceText(krCacheUniverse?.source_trace, "source_document_id") ?? "source_trace pending";
  const normalizedTicker = ticker?.trim().toUpperCase();
  const selectedUniverseRow = normalizedTicker
    ? krCacheUniverse?.rows.find((row) => row.ticker === normalizedTicker)
    : undefined;
  const selectedGapRefs = selectedUniverseRow?.gap_audit_refs ?? [];

  return (
    <section className="kr-source-readiness-card" data-testid={`${testIdPrefix}-source-readiness`}>
      <div className="kr-source-readiness-heading">
        <span>{title}</span>
        <strong data-testid={`${testIdPrefix}-selected-status`}>{selectedStatus}</strong>
      </div>
      <dl>
        <div data-testid={`${testIdPrefix}-selected-years`}>
          <dt>Selected years</dt>
          <dd>{selectedYears}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-selected-backend`}>
          <dt>Backend</dt>
          <dd>{selectedBackend}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-selected-numbers`}>
          <dt>Numbers</dt>
          <dd>{selectedNumbers}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-selected-missing`}>
          <dt>Missing</dt>
          <dd>{selectedMissing}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-universe-ready`}>
          <dt>Top 10 ready</dt>
          <dd>{universeReady}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-universe-mix`}>
          <dt>Coverage mix</dt>
          <dd>{universeMix}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-universe-quality`}>
          <dt>Universe quality</dt>
          <dd>{formatStatus(krCacheUniverse?.quality_status ?? krCacheUniverse?.coverage_status ?? "not loaded")}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-selected-ticker`}>
          <dt>Selected ticker</dt>
          <dd>{normalizedTicker ?? "-"}</dd>
        </div>
        <div data-testid={`${testIdPrefix}-gap-ref-count`}>
          <dt>Gap refs</dt>
          <dd>{selectedUniverseRow ? selectedGapRefs.length : "-"}</dd>
        </div>
      </dl>
      {selectedUniverseRow ? (
        <div className="kr-gap-ref-ledger" data-testid={`${testIdPrefix}-selected-gap-ledger`}>
          <div className="kr-gap-ref-summary">
            <span>{formatStatus(selectedUniverseRow.coverage_status)}</span>
            <strong>{selectedUniverseRow.market_gap_count} market / {selectedUniverseRow.financial_gap_count} metric</strong>
          </div>
          {selectedGapRefs.length ? (
            <ul>
              {selectedGapRefs.slice(0, 5).map((ref) => (
                <li key={ref.factId}>
                  <div>
                    <strong>{gapRefTitle(ref)}</strong>
                    <span>{gapRefDetail(ref)}</span>
                  </div>
                  <nav aria-label={`${gapRefTitle(ref)} audit links`}>
                    <a
                      href={auditFactHref(ref.factId)}
                      target="_blank"
                      rel="noreferrer"
                      data-testid={`${testIdPrefix}-gap-ref-${auditTestIdPart(ref.factId)}`}
                    >
                      Data Audit
                    </a>
                    {ref.sourceDocumentId ? (
                      <a
                        href={sourceDocumentHref(ref.sourceDocumentId)}
                        target="_blank"
                        rel="noreferrer"
                        data-testid={`${testIdPrefix}-gap-source-${auditTestIdPart(ref.sourceDocumentId)}`}
                      >
                        Source doc
                      </a>
                    ) : null}
                  </nav>
                </li>
              ))}
            </ul>
          ) : (
            <small>No gap refs for selected ticker.</small>
          )}
          {selectedGapRefs.length > 5 ? <small>+{selectedGapRefs.length - 5} more refs in Data Audit.</small> : null}
          {selectedUniverseRow.source_note ? <small>{selectedUniverseRow.source_note}</small> : null}
        </div>
      ) : null}
      <small data-testid={`${testIdPrefix}-universe-source-doc`}>{sourceDocumentId}</small>
    </section>
  );
}

function formatStatus(value: string) {
  return value.replace(/_/g, " ");
}

function formatYearList(years: number[]) {
  if (!years.length) {
    return "none";
  }
  return years.join(", ");
}

function formatMissingYears(coverage: KrValuationCacheCoverage | null | undefined) {
  if (!coverage) {
    return "not loaded";
  }
  const marketYears = coverage.missing_years?.market_input ?? [];
  const metricYears = coverage.missing_years?.financial_metric ?? [];
  if (!marketYears.length && !metricYears.length) {
    return "none";
  }
  const parts = [];
  if (marketYears.length) {
    parts.push(`market ${marketYears.join(", ")}`);
  }
  if (metricYears.length) {
    parts.push(`metric ${metricYears.join(", ")}`);
  }
  return parts.join(" / ");
}

function traceText(trace: Record<string, unknown> | undefined, key: string) {
  const value = trace?.[key];
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

function gapRefTitle(ref: KrValuationCacheGapAuditRef) {
  return [ref.label ?? ref.scope, ref.fiscalYear ? `FY${ref.fiscalYear}` : null].filter(Boolean).join(" ");
}

function gapRefDetail(ref: KrValuationCacheGapAuditRef) {
  return [
    formatStatus(ref.status ?? ref.qualityStatus ?? "gap_ref"),
    ref.reason ?? ref.nextAction ?? ref.method ?? ref.sourceType
  ]
    .filter(Boolean)
    .join(" / ");
}
