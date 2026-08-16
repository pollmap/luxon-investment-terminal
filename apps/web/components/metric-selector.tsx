"use client";

import { Check, ChevronDown, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

type MetricOption = {
  value: string;
  label: string;
  group: string;
  disabledHint?: string;
};

export function MetricSelector({
  value,
  groups,
  options,
  disabledReasonFor,
  onChange
}: {
  value: string;
  groups: readonly string[];
  options: readonly MetricOption[];
  disabledReasonFor: (option: MetricOption) => string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = useMemo(
    () => options.find((option) => option.value === value),
    [options, value]
  );
  const coverage = useMemo(() => {
    const rows = options.map((option) => ({
      option,
      disabledReason: disabledReasonFor(option)
    }));
    return {
      available: rows.filter((row) => !row.disabledReason).length,
      locked: rows.filter((row) => row.disabledReason).length
    };
  }, [disabledReasonFor, options]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function closeOnOutsidePointer(event: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="metric-selector" ref={rootRef}>
      <span>Price Correlated With</span>
      <select
        aria-label="Metric"
        className="metric-native-select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {groups.map((group) => (
          <optgroup key={group} label={group}>
            {options.filter((option) => option.group === group).map((option) => {
              const disabledReason = disabledReasonFor(option);
              return (
                <option key={option.value} value={option.value} disabled={Boolean(disabledReason)}>
                  {option.label}{disabledReason ? ` (${disabledReason})` : ""}
                </option>
              );
            })}
          </optgroup>
        ))}
      </select>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        className="metric-selector-trigger"
        type="button"
        onClick={() => setOpen((state) => !state)}
      >
        <span className="metric-selector-icon"><Sparkles size={14} /></span>
        <strong>{selected?.label ?? "Select metric"}</strong>
        <ChevronDown size={16} />
      </button>
      {open ? (
        <div className="metric-selector-menu" role="listbox" aria-label="Price correlated with">
          <div className="metric-selector-smart">
            <span>SMART METRIC</span>
            <em>{value === "smart_metric" ? "on" : "off"}</em>
          </div>
          <p className="metric-selector-source-guard" data-testid="metric-selector-source-guard">
            No source_trace = disabled. Metric values require source-backed rows.
          </p>
          <div className="metric-selector-coverage" data-testid="metric-selector-coverage">
            <div>
              <span>Available</span>
              <strong data-testid="metric-selector-available-count">{coverage.available}</strong>
            </div>
            <div>
              <span>Locked</span>
              <strong data-testid="metric-selector-locked-count">{coverage.locked}</strong>
            </div>
            <div>
              <span>Selected</span>
              <strong>{selected?.group ?? "Metric"}</strong>
            </div>
          </div>
          {groups.map((group) => {
            const groupOptions = options.filter((option) => option.group === group);
            return (
              <div className="metric-selector-group" key={group}>
                <p>{group}</p>
                {groupOptions.map((option) => {
                  const disabledReason = disabledReasonFor(option);
                  const selectedOption = option.value === value;
                  return (
                    <button
                      key={option.value}
                      aria-selected={selectedOption}
                      className={selectedOption ? "selected" : ""}
                      disabled={Boolean(disabledReason)}
                      role="option"
                      data-testid={`metric-option-${option.value}`}
                      type="button"
                      title={disabledReason || option.label}
                      onClick={() => {
                        if (disabledReason) {
                          return;
                        }
                        onChange(option.value);
                        setOpen(false);
                      }}
                    >
                      <span className="metric-option-copy">
                        <span>{option.label}</span>
                        <small data-testid={`metric-option-source-${option.value}`}>
                          {metricSourceNote(option, disabledReason)}
                        </small>
                      </span>
                      {disabledReason ? (
                        <em data-testid={`metric-option-reason-${option.value}`}>{disabledReason}</em>
                      ) : null}
                      {selectedOption ? <Check size={16} /> : null}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function metricSourceNote(option: MetricOption, disabledReason: string) {
  if (disabledReason) {
    return "waiting for source-backed row";
  }
  if (option.value === "adjusted_operating") {
    return "adjusted earnings engine";
  }
  if (option.value === "diluted_eps") {
    return "GAAP filing metric";
  }
  if (option.value === "sales_share") {
    return "revenue/share source trace";
  }
  return "source trace ready";
}
