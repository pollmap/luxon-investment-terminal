import type { AuditRow } from "../lib/terminal-types";

export type GraphKeyLedgerItem = {
  key: string;
  label: string;
  swatchClass: string;
  visible: boolean;
  value: string;
  formula: string;
  sourceLabel: string;
  auditRow?: AuditRow;
};

export function GraphKeyLedger({
  items,
  factQueryString,
  buildFactHref,
  onInspectFact
}: {
  items: GraphKeyLedgerItem[];
  factQueryString?: string;
  buildFactHref: (factId: string, queryString?: string) => string;
  onInspectFact?: (factId: string) => void;
}) {
  return (
    <div className="graph-key-ledger legend" data-testid="graph-key-ledger">
      {items.map((item) => (
        <div
          key={item.key}
          className={`graph-key-row ${item.visible ? "on" : "off"}`}
          data-testid={`graph-key-row-${item.key}`}
        >
          <div className="graph-key-top">
            <span className={`graph-key-swatch ${item.swatchClass}`} />
            <strong>{item.label} {item.value}</strong>
            <em>{item.visible ? "on" : "off"}</em>
          </div>
          <p>{item.formula}</p>
          <div className="graph-key-source">
            <span>{item.sourceLabel}</span>
            {item.auditRow ? (
              <div className="graph-key-actions">
                <button
                  type="button"
                  data-testid={`graph-key-inspect-${item.key}`}
                  onClick={() => onInspectFact?.(item.auditRow!.fact_id)}
                >
                  Inspect
                </button>
                <a href={buildFactHref(item.auditRow.fact_id, factQueryString)} target="_blank" rel="noreferrer">
                  Open fact
                </a>
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
