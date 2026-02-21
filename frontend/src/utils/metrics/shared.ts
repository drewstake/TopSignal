export interface DerivedMetricValue {
  value: number | null;
  missingReason?: string;
}

export const EPSILON = 1e-9;

export function metric(value: number): DerivedMetricValue {
  return { value };
}

export function missingMetric(missingReason: string): DerivedMetricValue {
  return {
    value: null,
    missingReason,
  };
}

export function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

export function average(values: number[]): number | null {
  if (values.length === 0) {
    return null;
  }
  return sum(values) / values.length;
}

export function median(values: number[]): number | null {
  if (values.length === 0) {
    return null;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[middle - 1] + sorted[middle]) / 2;
  }
  return sorted[middle];
}

export function percentile(values: number[], percentileValue: number): number | null {
  if (values.length === 0) {
    return null;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const clampedPercentile = Math.max(0, Math.min(100, percentileValue));
  const rank = (clampedPercentile / 100) * (sorted.length - 1);
  const lowerIndex = Math.floor(rank);
  const upperIndex = Math.ceil(rank);
  if (lowerIndex === upperIndex) {
    return sorted[lowerIndex];
  }

  const interpolation = rank - lowerIndex;
  return sorted[lowerIndex] + (sorted[upperIndex] - sorted[lowerIndex]) * interpolation;
}
