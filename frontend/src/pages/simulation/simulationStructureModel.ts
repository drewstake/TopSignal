import type { Direction, SimulationCandle } from './simulationModel';

export const STRUCTURE_BAR_LIMIT = 64;
export const ZONE_LIMIT = 12;
export const LEVEL_LIMIT = 20;
export const SIGNAL_LIMIT = 64;
export type StructureMode = 'zone-limit' | 'zone-rejection' | 'level-rejection' | 'breakout-retest';
export interface PriceZone {
  id: number; side: Direction; low: number; high: number; createdAt: number;
  tests: number; lastTest: number; touching: boolean;
}
export interface PriceLevel {
  id: number; side: Direction; price: number; createdAt: number;
  lastTest: number; lastPivot: number; tests: number; touching: boolean;
}
export interface StructureSetup {
  signal: string; source: 'zone' | 'level' | 'breakout'; structureId: number;
  side: Direction; entry: number; stop: number; createdAt: number; expires: number;
}
export interface StructureState {
  version: 1; mode: StructureMode; randomState: number;
  lookback: number; atrPeriod: number; baseLength: number; pivotLeft: number; pivotRight: number;
  tolerance: number; departure: number; compactness: number; separation: number; maxAge: number; maxTests: number;
  checkedThrough: number; lastBar: number; serial: number; atr: number;
  bars: SimulationCandle[]; zones: PriceZone[]; levels: PriceLevel[]; used: string[];
  breakout?: { levelId: number; side: Direction; price: number; tolerance: number; brokenAt: number; expires: number };
  setup?: StructureSetup; entryOrder?: number;
  watched: string; reason: string;
}
