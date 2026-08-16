import type {
  ChartReturnSelection,
  ForecastCalculationLine,
  ForecastMeta,
  PortfolioTransactionView,
  PricePoint,
  RecessionBand,
  ValuationRow
} from "./terminal-types";

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
export function currentValuationMultiple(rows: ValuationRow[]) {
  const latest = [...rows]
    .reverse()
    .find((row) => !row.forecast_flag && Number(row.price) > 0 && Number(row.metric) > 0);
  if (!latest) {
    return null;
  }
  return Number(latest.price) / Number(latest.metric);
}

export function latestHistoricalYear(rows: ValuationRow[]) {
  const latest = [...rows]
    .reverse()
    .find((row) => !row.forecast_flag && Number.isFinite(Number(row.fiscal_year)));
  return latest?.fiscal_year ?? null;
}

export function buildChartReturnSelection(rows: ValuationRow[], selectedYears: number[]): ChartReturnSelection | null {
  if (selectedYears.length < 2) {
    return null;
  }
  const [startYear, endYear] = [...selectedYears].sort((left, right) => left - right);
  const start = rows.find((row) => row.fiscal_year === startYear && !row.forecast_flag);
  const end = rows.find((row) => row.fiscal_year === endYear && !row.forecast_flag);
  if (!start || !end || endYear <= startYear) {
    return null;
  }
  const startPrice = Number(start.price);
  const endPrice = Number(end.price);
  const years = endYear - startYear;
  if (!Number.isFinite(startPrice) || !Number.isFinite(endPrice) || startPrice <= 0 || years <= 0) {
    return null;
  }
  const dividends = rows
    .filter((row) => !row.forecast_flag && row.fiscal_year > startYear && row.fiscal_year <= endYear)
    .reduce((total, row) => total + Number(row.dividend || 0), 0);
  const totalEndValue = endPrice + dividends;
  return {
    startYear,
    endYear,
    years,
    startPrice,
    endPrice,
    dividends,
    priceReturnPct: (endPrice / startPrice - 1) * 100,
    totalReturnPct: (totalEndValue / startPrice - 1) * 100,
    annualizedPriceReturnPct: (Math.pow(endPrice / startPrice, 1 / years) - 1) * 100,
    annualizedTotalReturnPct: (Math.pow(totalEndValue / startPrice, 1 / years) - 1) * 100
  };
}

export function isYearInReturnRange(year: number, selectedYears: number[]) {
  if (!selectedYears.length) {
    return false;
  }
  if (selectedYears.length === 1) {
    return year === selectedYears[0];
  }
  const [start, end] = [...selectedYears].sort((left, right) => left - right);
  return year >= start && year <= end;
}

export function latestDividendRatioMetrics(rows: ValuationRow[]) {
  const latest = [...rows]
    .reverse()
    .find((row) => !row.forecast_flag && Number(row.dividend) >= 0 && Number(row.metric) > 0 && Number(row.price) > 0);
  if (!latest) {
    return { payoutRatioPct: null, dividendYieldPct: null };
  }
  return {
    payoutRatioPct: Number(latest.dividend) / Number(latest.metric) * 100,
    dividendYieldPct: Number(latest.dividend) / Number(latest.price) * 100
  };
}

export function formatMaybePercent(value: number | null) {
  return value !== null && Number.isFinite(value) ? `${value.toFixed(1)}%` : "-";
}

export function buildLinePoints(
  rows: ValuationRow[],
  maxPrice: number,
  currentMultiple: number | null,
  customMultiple: number | null,
  pricePoints: PricePoint[] = []
) {
  const toPoint = (index: number, value: number) => {
    const x = rows.length <= 1 ? 50 : ((index + 0.5) / rows.length) * 100;
    const y = 100 - Math.min(95, Math.max(5, (value / maxPrice) * 82));
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  };
  const toSeries = (valueFor: (row: ValuationRow) => number, predicate: (row: ValuationRow) => boolean = () => true) =>
    rows
      .map((row, index) => predicate(row) ? toPoint(index, valueFor(row)) : null)
      .filter((point): point is string => Boolean(point))
      .join(" ");
  return {
    price: buildPricePointLine(rows, pricePoints, maxPrice) || toSeries((row) => Number(row.price), (row) => !row.forecast_flag),
    fair: toSeries((row) => Number(row.fair_value_price)),
    normal: toSeries((row) => Number(row.metric) * Number(row.normal_multiple ?? 0)),
    current: currentMultiple ? toSeries((row) => Number(row.metric) * currentMultiple) : "",
    custom: customMultiple ? toSeries((row) => Number(row.metric) * customMultiple) : "",
    dividend: toSeries((row) => Number(row.dividend) * 15)
  };
}

export function buildPricePointLine(rows: ValuationRow[], pricePoints: PricePoint[], maxPrice: number) {
  return pricePoints
    .map((point) => {
      const closePrice = toNumberOrNull(point.close_price);
      if (closePrice === null || closePrice <= 0) {
        return null;
      }
      const x = chartXForDate(rows, point.date);
      if (x === null) {
        return null;
      }
      const y = 100 - Math.min(95, Math.max(5, (closePrice / maxPrice) * 82));
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .filter((point): point is string => Boolean(point))
    .join(" ");
}

export function buildTradeOverlayPoints(
  rows: ValuationRow[],
  transactions: PortfolioTransactionView[],
  maxPrice: number,
  pricePoints: PricePoint[] = []
) {
  return transactions
    .map((transaction) => {
      const date = new Date(transaction.date);
      const year = date.getUTCFullYear();
      const rowIndex = rows.findIndex((row) => row.fiscal_year === year);
      const row = rowIndex >= 0 ? rows[rowIndex] : undefined;
      if (!row || Number.isNaN(date.getTime())) {
        return null;
      }
      const transactionPrice = toNumberOrNull(transaction.price);
      const chartPrice = transactionPrice !== null && transactionPrice > 0
        ? transactionPrice
        : pricePointAtOrBefore(pricePoints, transaction.date) ?? toNumberOrNull(row.price);
      if (chartPrice === null || chartPrice <= 0) {
        return null;
      }
      const x = chartXForDate(rows, transaction.date) ?? (rows.length <= 1 ? 50 : ((rowIndex + 0.5) / rows.length) * 100);
      const y = 100 - Math.min(95, Math.max(5, (chartPrice / maxPrice) * 82));
      return {
        x: x.toFixed(2),
        y: y.toFixed(2),
        transaction
      };
    })
    .filter((point): point is { x: string; y: string; transaction: PortfolioTransactionView } => Boolean(point));
}

export function pricePointAtOrBefore(pricePoints: PricePoint[], rawDate: string) {
  const targetTime = new Date(rawDate).getTime();
  if (!pricePoints.length || Number.isNaN(targetTime)) {
    return null;
  }
  let selected: { time: number; closePrice: number } | null = null;
  for (const point of pricePoints) {
    const time = new Date(point.date).getTime();
    const closePrice = toNumberOrNull(point.close_price);
    if (Number.isNaN(time) || time > targetTime || closePrice === null || closePrice <= 0) {
      continue;
    }
    if (!selected || time > selected.time) {
      selected = { time, closePrice };
    }
  }
  return selected?.closePrice ?? null;
}

export function chartXForDate(rows: ValuationRow[], rawDate: string) {
  const date = new Date(rawDate);
  if (!rows.length || Number.isNaN(date.getTime())) {
    return null;
  }
  const year = date.getUTCFullYear();
  const yearIndex = rows.findIndex((row) => !row.forecast_flag && row.fiscal_year === year);
  if (yearIndex < 0) {
    return null;
  }
  const monthFraction = (date.getUTCMonth() + Math.max(0, date.getUTCDate() - 1) / 31) / 12;
  const x = ((yearIndex + monthFraction) / rows.length) * 100;
  return Math.min(100, Math.max(0, x));
}

export function buildRatioLinePoints(rows: ValuationRow[], valueFor: (row: ValuationRow) => number, defaultMax: number) {
  const values = rows
    .map((row, index) => ({ index, value: valueFor(row) }))
    .filter((point) => Number.isFinite(point.value) && point.value >= 0);
  if (!values.length) {
    return "";
  }
  const maxValue = Math.max(defaultMax, ...values.map((point) => point.value), 1);
  return values
    .map((point) => {
      const x = rows.length <= 1 ? 50 : ((point.index + 0.5) / rows.length) * 100;
      const y = 88 - Math.min(72, (point.value / maxValue) * 72);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function buildRecessionRects(rows: ValuationRow[], bands: RecessionBand[]) {
  if (!rows.length || !bands.length) {
    return [];
  }
  const years = rows.map((row) => Number(row.fiscal_year)).filter(Number.isFinite);
  const firstYear = Math.min(...years);
  const lastYear = Math.max(...years);
  const minXYear = firstYear - 0.5;
  const maxXYear = lastYear + 0.5;
  const yearSpan = Math.max(1, maxXYear - minXYear);
  return bands
    .map((band) => {
      const start = dateToYearFraction(band.start_date);
      const end = band.end_date ? dateToYearFraction(band.end_date) : lastYear + 0.5;
      if (start === null || end === null || end < minXYear || start > maxXYear) {
        return null;
      }
      const startX = Math.max(0, (Math.max(start, minXYear) - minXYear) / yearSpan * 100);
      const endX = Math.min(100, (Math.min(end, maxXYear) - minXYear) / yearSpan * 100);
      return {
        x: startX.toFixed(2),
        width: Math.max(2.4, endX - startX).toFixed(2),
        label: `${band.series_id}-${band.start_date}-${band.end_date ?? "open"}`
      };
    })
    .filter((rect): rect is { x: string; width: string; label: string } => Boolean(rect));
}

export function dateToYearFraction(raw: string) {
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.getUTCFullYear() + date.getUTCMonth() / 12 + Math.max(0, date.getUTCDate() - 1) / 365;
}

export function buildMetricAreaPath(rows: ValuationRow[], maxPrice: number) {
  const points = rows
    .map((row, index) => {
      const value = Number(row.fair_value_price || Number(row.metric) * Number(row.fair_multiple ?? 0));
      if (!Number.isFinite(value)) {
        return null;
      }
      const x = rows.length <= 1 ? 50 : ((index + 0.5) / rows.length) * 100;
      const y = 100 - Math.min(95, Math.max(5, (value / maxPrice) * 82));
      return { x, y };
    })
    .filter((point): point is { x: number; y: number } => Boolean(point));
  if (!points.length) {
    return "";
  }
  const line = points.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
  const first = points[0];
  const last = points[points.length - 1];
  return `M ${first.x.toFixed(2)} 100 ${line} L ${last.x.toFixed(2)} 100 Z`;
}

export function buildScenarioLinePoints(rows: ValuationRow[], lines: ForecastCalculationLine[], maxPrice: number) {
  return lines.map((line) => ({
    label: line.label,
    points: line.points.map((point) => {
      const rowIndex = rows.findIndex((row) => row.fiscal_year === point.fiscal_year);
      const index = rowIndex >= 0 ? rowIndex : rows.length - 1;
      const x = rows.length <= 1 ? 50 : ((index + 0.5) / rows.length) * 100;
      const y = 100 - Math.min(95, Math.max(5, (Number(point.target_price) / maxPrice) * 82));
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ")
  }));
}

export function maxChartValue(
  rows: ValuationRow[],
  forecastMeta: ForecastMeta,
  currentMultiple: number | null,
  customMultiple: number | null,
  pricePoints: PricePoint[] = []
) {
  const values = rows.flatMap((row) => [
    Number(row.price),
    Number(row.fair_value_price),
    Number(row.metric) * Number(row.normal_multiple ?? 0),
    currentMultiple ? Number(row.metric) * currentMultiple : 0,
    customMultiple ? Number(row.metric) * customMultiple : 0,
    Number(row.dividend) * 15
  ]);
  values.push(...pricePoints.map((point) => Number(point.close_price)));
  for (const line of forecastCalculationLines(forecastMeta, rows)) {
    for (const point of line.points) {
      values.push(Number(point.target_price));
    }
  }
  return Math.max(...values.filter((value) => Number.isFinite(value) && value > 0), 1);
}

export function forecastCalculationLines(forecastMeta: ForecastMeta, rows?: ValuationRow[]) {
  const lines = Array.isArray(forecastMeta.calculation_lines) ? forecastMeta.calculation_lines : [];
  if (lines.length || !rows?.length) {
    return lines;
  }
  const forecastRows = rows.filter((row) => row.forecast_flag);
  if (!forecastRows.length) {
    return [];
  }
  const center = Number(forecastMeta.target_multiple || forecastRows[0].fair_multiple || 15);
  return Array.from({ length: 11 }, (_, index) => {
    const multiple = Math.max(1, center - 5 + index);
    return {
      multiple: multiple.toFixed(2),
      label: `${multiple.toFixed(0)}x`,
      points: forecastRows.map((row) => ({
        fiscal_year: row.fiscal_year,
        target_price: (Number(row.metric) * multiple).toFixed(2)
      }))
    };
  });
}
