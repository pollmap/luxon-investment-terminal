"use client";

import { Eye, EyeOff } from "lucide-react";

export function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <img src="/valuetrace-mark.svg" alt="" width={34} height={34} />
    </div>
  );
}

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-chip">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: () => void }) {
  return (
    <button className={`toggle ${value ? "on" : ""}`} onClick={onChange} type="button">
      {value ? <Eye size={14} /> : <EyeOff size={14} />}
      {label}
    </button>
  );
}

export function NumberControl({
  label,
  value,
  suffix,
  onChange
}: {
  label: string;
  value: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="number-control">
      <span>{label}</span>
      <input
        aria-label={label}
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {suffix ? <em>{suffix}</em> : null}
    </label>
  );
}
