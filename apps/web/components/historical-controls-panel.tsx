"use client";

import { MetricSelector } from "./metric-selector";
import { forecastCases, forecastModes, metricOptionGroups, metricOptions } from "../lib/terminal-config";
import type { ChartLayout } from "../lib/terminal-types";

const historicalRangeButtons = [
  { label: "MAX", value: "max" },
  { label: "18Y", value: "18" },
  { label: "16Y", value: "16" },
  { label: "14Y", value: "14" },
  { label: "12Y", value: "12" },
  { label: "10Y", value: "10" },
  { label: "8Y", value: "8" },
  { label: "6Y", value: "6" },
  { label: "4Y", value: "4" },
  { label: "2Y", value: "2" },
  { label: "1Y", value: "1" }
] as const;

type HistoricalControlsPanelProps = {
  metric: string;
  forecastMode: string;
  forecastCase: string;
  forecastYears: number;
  rangeMode: string;
  rangeStartYear: string;
  rangeEndYear: string;
  normalMultipleYears: number;
  growth: number;
  targetMultiple: number;
  chartSettingsOpen: boolean;
  displayRangeSummary: string;
  chartLayoutName: string;
  selectedChartLayoutId: string;
  chartLayouts: ChartLayout[];
  chartLayoutStatus: string;
  disabledReasonForMetric: (option: (typeof metricOptions)[number]) => string;
  onMetricChange: (value: string) => void;
  onForecastModeChange: (value: string) => void;
  onForecastCaseChange: (value: string) => void;
  onForecastYearsChange: (value: number) => void;
  onApplyRangeMode: (value: string) => void;
  onRangeStartYearChange: (value: string) => void;
  onRangeEndYearChange: (value: string) => void;
  onNormalMultipleYearsChange: (value: number) => void;
  onGrowthChange: (value: number) => void;
  onTargetMultipleChange: (value: number) => void;
  onToggleChartSettings: () => void;
  onChartLayoutNameChange: (value: string) => void;
  onSaveCurrentChartLayout: () => void;
  onApplyChartLayout: (id: string) => void;
};

export function HistoricalControlsPanel({
  metric,
  forecastMode,
  forecastCase,
  forecastYears,
  rangeMode,
  rangeStartYear,
  rangeEndYear,
  normalMultipleYears,
  growth,
  targetMultiple,
  chartSettingsOpen,
  displayRangeSummary,
  chartLayoutName,
  selectedChartLayoutId,
  chartLayouts,
  chartLayoutStatus,
  disabledReasonForMetric,
  onMetricChange,
  onForecastModeChange,
  onForecastCaseChange,
  onForecastYearsChange,
  onApplyRangeMode,
  onRangeStartYearChange,
  onRangeEndYearChange,
  onNormalMultipleYearsChange,
  onGrowthChange,
  onTargetMultipleChange,
  onToggleChartSettings,
  onChartLayoutNameChange,
  onSaveCurrentChartLayout,
  onApplyChartLayout
}: HistoricalControlsPanelProps) {
  return (
    <section
      className={`historical-controls-band ${chartSettingsOpen ? "settings-open" : "settings-closed"} ${rangeMode === "custom" ? "range-custom" : ""}`}
      data-testid="historical-controls-band"
      aria-label="Historical valuation controls"
    >
      <section className="control-strip">
        <MetricSelector
          value={metric}
          groups={metricOptionGroups}
          options={metricOptions}
          disabledReasonFor={(option) => disabledReasonForMetric(option as (typeof metricOptions)[number])}
          onChange={onMetricChange}
        />
        <label>
          Calculator
          <select aria-label="Forecast mode" value={forecastMode} onChange={(event) => onForecastModeChange(event.target.value)}>
            {forecastModes.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Forecast case
          <select aria-label="Forecast case" value={forecastCase} onChange={(event) => onForecastCaseChange(event.target.value)}>
            {forecastCases.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          Forecast
          <input type="range" min="1" max="5" value={forecastYears} onChange={(event) => onForecastYearsChange(Number(event.target.value))} />
          <span>{forecastYears}Y</span>
        </label>
        <label>
          Range
          <select aria-label="Historical range" value={rangeMode} onChange={(event) => onApplyRangeMode(event.target.value)}>
            <option value="max">MAX</option>
            <option value="5">5Y</option>
            <option value="3">3Y</option>
            <option value="1">1Y</option>
            <option value="custom">Custom</option>
          </select>
        </label>
        <label>
          Start
          <input
            aria-label="Range start year"
            type="number"
            value={rangeStartYear}
            placeholder="auto"
            onChange={(event) => onRangeStartYearChange(event.target.value)}
          />
        </label>
        <label>
          End
          <input
            aria-label="Range end year"
            type="number"
            value={rangeEndYear}
            placeholder="auto"
            onChange={(event) => onRangeEndYearChange(event.target.value)}
          />
        </label>
        <label>
          Normal P/E window
          <select value={normalMultipleYears} onChange={(event) => onNormalMultipleYearsChange(Number(event.target.value))}>
            {[1, 2, 3, 5, 7, 10, 15, 20].map((years) => (
              <option key={years} value={years}>{years}FY</option>
            ))}
          </select>
        </label>
        <label>
          EPS growth
          <input type="number" value={growth} onChange={(event) => onGrowthChange(Number(event.target.value))} />
          <span>%</span>
        </label>
        <label>
          Target / custom P/E
          <input
            type="number"
            value={targetMultiple}
            onChange={(event) => onTargetMultipleChange(Number(event.target.value))}
          />
        </label>
      </section>

      <section className="historical-period-strip" aria-label="Historical period controls" data-testid="historical-period-strip">
        <span>PERIOD: {rangeMode === "custom" ? "CUSTOM" : rangeMode === "max" ? "MAX" : `${rangeMode}Y`}</span>
        <div>
          {historicalRangeButtons.map((button) => (
            <button
              key={button.value}
              type="button"
              className={rangeMode === button.value ? "active" : ""}
              aria-pressed={rangeMode === button.value}
              aria-label={`Set period ${button.label}`}
              onClick={() => onApplyRangeMode(button.value)}
            >
              {button.label}
            </button>
          ))}
        </div>
        <label className="period-dropdown-control">
          <span>Period dropdown</span>
          <select aria-label="Period dropdown" value={rangeMode} onChange={(event) => onApplyRangeMode(event.target.value)}>
            {historicalRangeButtons.map((button) => (
              <option key={button.value} value={button.value}>
                PERIOD: {button.label}
              </option>
            ))}
            <option value="custom">PERIOD: CUSTOM</option>
          </select>
        </label>
        <button
          type="button"
          className={`chart-settings-band-trigger ${chartSettingsOpen ? "on" : ""}`}
          data-testid="chart-settings-band-trigger"
          aria-expanded={chartSettingsOpen}
          aria-controls="chart-settings-drawer"
          onClick={onToggleChartSettings}
        >
          Chart settings
        </button>
        <button
          type="button"
          className={`choose-dates-toggle ${rangeMode === "custom" ? "on" : ""}`}
          aria-pressed={rangeMode === "custom"}
          onClick={() => onApplyRangeMode(rangeMode === "custom" ? "max" : "custom")}
        >
          <i aria-hidden="true" />
          Choose dates
        </button>
        <small>{displayRangeSummary}</small>
      </section>

      <section className="layout-strip" aria-label="Chart layout presets">
        <label>
          Layout name
          <input
            aria-label="Layout name"
            value={chartLayoutName}
            onChange={(event) => onChartLayoutNameChange(event.target.value)}
          />
        </label>
        <button type="button" onClick={onSaveCurrentChartLayout}>
          Save layout
        </button>
        <label>
          Saved layout
          <select
            aria-label="Saved chart layout"
            value={selectedChartLayoutId}
            onChange={(event) => onApplyChartLayout(event.target.value)}
          >
            <option value="">Select layout</option>
            {chartLayouts.map((layout) => (
              <option key={layout.id} value={layout.id}>
                {layout.name} - {layout.ticker}
              </option>
            ))}
          </select>
        </label>
        <span>{chartLayoutStatus}</span>
      </section>
    </section>
  );
}
