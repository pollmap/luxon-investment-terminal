"use client";

import { useEffect, useState } from "react";

import { publicTraceSummary } from "../lib/audit-utils";
import type {
  ConsensusContractData,
  ContractDataStatus,
  ContractEnvelope,
  ContractFactValue,
  PeerContractData,
  ProvidersContractData
} from "../lib/terminal-types";

type LoadState<T> =
  | { phase: "loading"; envelope: null; message: string }
  | { phase: "loaded"; envelope: ContractEnvelope<T>; message: string }
  | { phase: "error"; envelope: null; message: string };

type ContractRequestState<T> = LoadState<T> & { url: string };

const dataModeByStatus: Record<ContractDataStatus, string> = {
  ready: "source_backed",
  partial: "source_backed",
  configured: "configuration_only",
  stale: "source_backed",
  fixture_non_production: "fixture_non_production",
  missing_source: "unavailable",
  missing_contract: "unavailable",
  missing_key: "unavailable",
  rate_limited: "unavailable",
  upstream_error: "unavailable"
};

const unavailableStatuses = new Set<ContractDataStatus>([
  "missing_source",
  "missing_contract",
  "missing_key",
  "rate_limited",
  "upstream_error"
]);

const requiredTraceFields = [
  "source",
  "filing_id",
  "period",
  "available_at",
  "unit",
  "currency",
  "method",
  "formula"
] as const;

function useContract<T>(url: string): LoadState<T> {
  const [state, setState] = useState<ContractRequestState<T>>({
    url,
    phase: "loading",
    envelope: null,
    message: "Loading source contract…"
  });

  useEffect(() => {
    const controller = new AbortController();
    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        const payload = parseContractEnvelope<T>(await response.json());
        if (!payload) {
          throw new Error("The API response failed the LUXON contract validation.");
        }
        if (!response.ok) {
          throw new Error(payload.state?.reason ?? `Request failed with ${response.status}`);
        }
        setState({ url, phase: "loaded", envelope: payload, message: payload.state.reason ?? "" });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setState({
          url,
          phase: "error",
          envelope: null,
          message: error instanceof Error ? error.message : "The source contract could not be loaded."
        });
      });
    return () => controller.abort();
  }, [url]);

  return state.url === url
    ? state
    : { phase: "loading", envelope: null, message: "Loading source contract…" };
}

export function ConsensusPanel({ ticker }: { ticker: string }) {
  const result = useContract<ConsensusContractData>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/consensus`
  );
  const envelope = result.envelope;
  const data = sourceBackedConsensusData(envelope);

  return (
    <section className="single-panel contract-panel" data-testid="consensus-contract-panel">
      <ContractHeader
        title="Consensus"
        description="Point-in-time analyst estimates only. Manual assumptions remain a separate evidence lane."
        status={envelope?.state.status ?? result.phase}
      />
      {!data ? (
        <ContractEmpty message={blockedDataMessage(envelope, result.message)} />
      ) : (
        <>
          <div className="quality-ledger">
            <div><strong>{data.company_id}</strong><span>{data.metric_name}</span><em>FY{data.forecast_year}</em></div>
            <div><strong>{data.provider}</strong><span>Provider</span><em>{data.evidence_kind}</em></div>
            <div><strong>{data.cases.length}</strong><span>Validated cases</span><em>{data.quality_status}</em></div>
          </div>
          <table className="terminal-table wide" aria-label={`${ticker} consensus cases`}>
            <thead><tr><th>Case</th><th>Estimate EPS</th><th>Growth</th><th>Basis</th><th>Period</th><th>Quality</th></tr></thead>
            <tbody>
              {data.cases.map((row) => (
                <tr key={row.case}>
                  <td>{row.case}</td>
                  <td>{formatFact(row.estimate_eps.value, row.estimate_eps.currency)}</td>
                  <td>{formatFact(row.growth_rate_pct?.value ?? null, "%")}</td>
                  <td>{row.assumption_type}</td>
                  <td>{row.estimate_eps.period ?? "-"}</td>
                  <td>{row.quality_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="source-box">
            <strong>Consensus source trace</strong>
            <code>{JSON.stringify(publicTraceSummary(data.cases[0]?.estimate_eps.source_trace ?? undefined), null, 2)}</code>
          </div>
        </>
      )}
    </section>
  );
}

export function PeersPanel({ ticker }: { ticker: string }) {
  const [kind, setKind] = useState<"business" | "valuation">("business");
  const result = useContract<PeerContractData>(
    `/api/v1/companies/${encodeURIComponent(ticker)}/peers?kind=${kind}`
  );
  const envelope = result.envelope;
  const data = sourceBackedPeerData(envelope);

  return (
    <section className="single-panel contract-panel" data-testid="peers-contract-panel">
      <ContractHeader
        title="Peers"
        description="Business competitors and valuation comparables are governed as separate, source-backed sets."
        status={envelope?.state.status ?? result.phase}
      />
      <div className="contract-toggle" role="group" aria-label="Peer relationship kind">
        <button type="button" className={kind === "business" ? "active" : ""} aria-pressed={kind === "business"} onClick={() => setKind("business")}>Business peers</button>
        <button type="button" className={kind === "valuation" ? "active" : ""} aria-pressed={kind === "valuation"} onClick={() => setKind("valuation")}>Valuation peers</button>
      </div>
      {!data ? (
        <ContractEmpty message={blockedDataMessage(envelope, result.message)} />
      ) : (
        <table className="terminal-table wide" aria-label={`${ticker} ${kind} peers`}>
          <thead><tr><th>Company</th><th>Relationship</th><th>Metrics</th><th>Source</th></tr></thead>
          <tbody>
            {data.peers.map((peer) => (
              <tr key={peer.company_id}>
                <td><strong>{peer.name}</strong><br /><small>{peer.company_id}</small></td>
                <td>{peer.relationship}</td>
                <td>{peer.facts.length ? peer.facts.map((fact) => `${fact.metric}: ${formatFact(fact.value, fact.unit)}`).join(" · ") : "No comparable facts"}</td>
                <td>{String(peer.source_trace.source ?? peer.source_trace.method ?? "source trace")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ProviderStatusPanel() {
  const result = useContract<ProvidersContractData>("/api/v1/system/providers");
  const envelope = result.envelope;
  const data = availablePayload(envelope);

  return (
    <section className="single-panel contract-panel" data-testid="provider-status-panel">
      <ContractHeader
        title="Provider readiness"
        description="Configuration state is separate from live reachability and source-row coverage. Secret values are never returned."
        status={envelope?.state.status ?? result.phase}
      />
      {!data ? (
        <ContractEmpty message={blockedDataMessage(envelope, result.message)} />
      ) : (
        <table className="terminal-table wide" aria-label="Provider readiness">
          <thead><tr><th>Provider</th><th>Status</th><th>Contract</th><th>Configured</th><th>Capabilities</th><th>Required settings</th></tr></thead>
          <tbody>
            {data.providers.map((provider) => (
              <tr key={provider.provider_id}>
                <td><strong>{provider.label}</strong><br /><small>{provider.provider_id}</small></td>
                <td>{provider.state.status}<br /><small>{provider.state.reason ?? provider.verification}</small></td>
                <td>{provider.contract_available ? "available" : "missing"}</td>
                <td>{provider.configured ? "yes" : "no"}</td>
                <td>{provider.capabilities.join(", ")}</td>
                <td>{provider.required_env.join(", ") || "none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ContractHeader({ title, description, status }: { title: string; description: string; status: string }) {
  return (
    <div className="panel-header contract-panel-header">
      <div><h1>{title}</h1><p>{description}</p></div>
      <span className={`contract-status contract-status-${status}`}>{status.replaceAll("_", " ")}</span>
    </div>
  );
}

function ContractEmpty({ message }: { message: string }) {
  return (
    <div className="contract-empty" role="status">
      <strong>No source-backed values displayed</strong>
      <p>{message}</p>
    </div>
  );
}

function formatFact(value: string | number | null, unit: string | null) {
  if (value === null || value === "") {
    return "-";
  }
  return unit === "%" || unit === "percent" ? `${value}%` : [value, unit].filter(Boolean).join(" ");
}

function parseContractEnvelope<T>(payload: unknown): ContractEnvelope<T> | null {
  if (!isRecord(payload) || !isRecord(payload.state) || !isRecord(payload.meta)) {
    return null;
  }
  const { status, available, data_mode: dataMode, reason } = payload.state;
  if (
    typeof status !== "string" ||
    !(status in dataModeByStatus) ||
    typeof available !== "boolean" ||
    typeof dataMode !== "string" ||
    dataModeByStatus[status as ContractDataStatus] !== dataMode ||
    (reason !== null && typeof reason !== "string") ||
    !("data" in payload)
  ) {
    return null;
  }
  const isUnavailable = unavailableStatuses.has(status as ContractDataStatus);
  if (available === isUnavailable || (isUnavailable ? payload.data !== null : payload.data === null)) {
    return null;
  }
  if (payload.data !== null && !isRecord(payload.data)) {
    return null;
  }
  return {
    data: payload.data as T | null,
    state: {
      status: status as ContractDataStatus,
      available,
      data_mode: dataMode,
      reason
    },
    meta: payload.meta
  };
}

function sourceBackedConsensusData(envelope: ContractEnvelope<ConsensusContractData> | null) {
  const data = sourceBackedPayload(envelope);
  if (
    !data ||
    !Array.isArray(data.cases) ||
    data.cases.length === 0 ||
    data.cases.some(
      (row) =>
        !isSourcedFact(row.estimate_eps, true) ||
        (row.growth_rate_pct !== null && !isSourcedFact(row.growth_rate_pct, true))
    )
  ) {
    return null;
  }
  return data;
}

function sourceBackedPeerData(envelope: ContractEnvelope<PeerContractData> | null) {
  const data = sourceBackedPayload(envelope);
  if (
    !data ||
    !Array.isArray(data.peers) ||
    data.peers.some(
      (peer) =>
        !hasStorageReadyTrace(peer.source_trace) ||
        !Array.isArray(peer.facts) ||
        peer.facts.some((fact) => !isSourcedFact(fact, false))
    )
  ) {
    return null;
  }
  return data;
}

function sourceBackedPayload<T>(envelope: ContractEnvelope<T> | null) {
  if (!envelope?.state.available || envelope.state.data_mode !== "source_backed") {
    return null;
  }
  return envelope.data;
}

function availablePayload<T>(envelope: ContractEnvelope<T> | null) {
  if (
    !envelope?.state.available ||
    (envelope.state.data_mode !== "configuration_only" && envelope.state.data_mode !== "source_backed")
  ) {
    return null;
  }
  return envelope.data;
}

function isSourcedFact(fact: ContractFactValue, valueRequired: boolean) {
  if (fact.value === null || fact.value === "") {
    return !valueRequired;
  }
  return hasStorageReadyTrace(fact.source_trace);
}

function hasStorageReadyTrace(trace: Record<string, unknown> | null) {
  return Boolean(
    trace &&
      requiredTraceFields.every((field) => {
        const value = trace[field];
        return value !== null && value !== undefined && String(value).trim().length > 0;
      })
  );
}

function blockedDataMessage<T>(envelope: ContractEnvelope<T> | null, fallback: string) {
  if (!envelope) {
    return fallback;
  }
  if (!envelope.state.available) {
    return envelope.state.reason ?? fallback;
  }
  if (envelope.state.data_mode !== "source_backed" && envelope.state.data_mode !== "configuration_only") {
    return `Data mode ${envelope.state.data_mode} is not permitted on this production surface.`;
  }
  return envelope.state.reason ?? "The response was blocked because required source-trace evidence is incomplete.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
