import type { CandlestickData, IChartApi, ISeriesApi, Logical, UTCTimestamp } from "lightweight-charts";

import type {
  DrawingOverlayState,
  RenderableDrawing,
  RenderableLivePriceLine,
} from "./BotChartPresentation";
import { toUtcTimestamp } from "./botChartData";

const MIN_DRAWING_SIZE_PX = 4;
const DRAWING_HIT_RADIUS_PX = 8;
const DRAWING_ENDPOINT_HIT_RADIUS_PX = 14;
const RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX = 22;

export type DrawingTool = "cursor" | "line" | "rectangle";
export type DrawingKind = Exclude<DrawingTool, "cursor">;
export type DrawingEditMode = "start" | "end" | "left" | "right" | "body";

export interface ChartPanePoint {
  x: number;
  y: number;
}

export interface DrawingPoint {
  logical: Logical;
  time: UTCTimestamp | null;
  price: number;
}

export interface DrawingShape {
  id: string;
  kind: DrawingKind;
  start: DrawingPoint;
  end: DrawingPoint;
}

export type DrawingDraft = DrawingShape;

export interface DrawingPlacementState {
  id: string;
  kind: DrawingKind;
  start: DrawingPoint;
  lastPanePoint: ChartPanePoint;
}

export interface DrawingEditState {
  id: string;
  mode: DrawingEditMode;
  pointerId: number;
  originPanePoint: ChartPanePoint;
  originalDrawing: DrawingShape;
}

export interface DrawingHitTarget {
  id: string;
  mode: DrawingEditMode;
}

export interface DrawingModifiers {
  ctrlKey: boolean;
  shiftKey: boolean;
}

export interface DrawingAnchorPreview {
  point: DrawingPoint;
}

interface DrawingChartHandles {
  chart: IChartApi;
  candleSeries: ISeriesApi<"Candlestick">;
}

interface DrawingLivePricePoint {
  timestamp: string;
  price: number;
}

export function chartPaneYFromPointerEvent(
  event: PointerEvent,
  container: HTMLDivElement,
  chart: IChartApi,
): number | null {
  const paneHeight = chart.paneSize().height;
  if (paneHeight <= 0) {
    return null;
  }

  const y = event.clientY - container.getBoundingClientRect().top;
  if (y < 0 || y > paneHeight) {
    return null;
  }

  return y;
}

export function normalizeDraggedLiquidityPrice(price: number): number {
  const roundedPrice = Math.round(price * 10_000) / 10_000;
  return Object.is(roundedPrice, -0) ? 0 : roundedPrice;
}

export function chartPanePointFromPointerEvent(
  event: PointerEvent,
  container: HTMLDivElement,
  chart: IChartApi,
  clamp = false,
): ChartPanePoint | null {
  const paneSize = chart.paneSize();
  if (paneSize.width <= 0 || paneSize.height <= 0) {
    return null;
  }

  const rect = container.getBoundingClientRect();
  const rawX = event.clientX - rect.left;
  const rawY = event.clientY - rect.top;
  if (!clamp && (rawX < 0 || rawX > paneSize.width || rawY < 0 || rawY > paneSize.height)) {
    return null;
  }

  return {
    x: clampNumber(rawX, 0, paneSize.width),
    y: clampNumber(rawY, 0, paneSize.height),
  };
}

export function drawingPointFromPanePoint(
  panePoint: ChartPanePoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): DrawingPoint | null {
  const logical = chart.timeScale().coordinateToLogical(panePoint.x);
  const time = chart.timeScale().coordinateToTime(panePoint.x);
  const price = candleSeries.coordinateToPrice(panePoint.y);
  if (logical === null || typeof price !== "number" || !Number.isFinite(price)) {
    return null;
  }

  return normalizeDrawingPoint({ logical, time: typeof time === "number" ? (time as UTCTimestamp) : null, price });
}

export function drawingPointToPanePoint(
  point: DrawingPoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): ChartPanePoint | null {
  const x = drawingPointXToPaneCoordinate(point, chart);
  const y = candleSeries.priceToCoordinate(point.price);
  if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  return { x, y };
}

function drawingPointXToPaneCoordinate(point: DrawingPoint, chart: IChartApi): number | null {
  if (point.time !== null) {
    const timeCoordinate = chart.timeScale().timeToCoordinate(point.time);
    if (timeCoordinate !== null && Number.isFinite(timeCoordinate)) {
      return timeCoordinate;
    }
  }

  const logical = Number(point.logical);
  if (!Number.isFinite(logical)) {
    return null;
  }

  const logicalCoordinate = chart.timeScale().logicalToCoordinate(logical as Logical);
  return logicalCoordinate !== null && Number.isFinite(logicalCoordinate) ? logicalCoordinate : null;
}

export function snapDrawingPointToCandle(
  panePoint: ChartPanePoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
  candles: CandlestickData<UTCTimestamp>[],
): DrawingPoint | null {
  let closestPoint: DrawingPoint | null = null;
  let closestDistance = Number.POSITIVE_INFINITY;

  for (const candle of candles) {
    const x = chart.timeScale().timeToCoordinate(candle.time);
    if (x === null || !Number.isFinite(x)) {
      continue;
    }
    const logical = chart.timeScale().coordinateToLogical(x);
    if (logical === null) {
      continue;
    }

    for (const price of [candle.high, candle.low, candle.open, candle.close]) {
      if (!Number.isFinite(price)) {
        continue;
      }
      const y = candleSeries.priceToCoordinate(price);
      if (y === null || !Number.isFinite(y)) {
        continue;
      }

      const distance = Math.hypot(x - panePoint.x, y - panePoint.y);
      if (distance < closestDistance) {
        closestDistance = distance;
        closestPoint = normalizeDrawingPoint({ logical, time: candle.time, price });
      }
    }
  }

  return closestPoint;
}

export function constrainDrawingEndPoint(
  kind: DrawingKind,
  start: ChartPanePoint,
  end: ChartPanePoint,
): ChartPanePoint {
  if (kind === "rectangle") {
    return constrainRectangleEndPoint(start, end);
  }
  return constrainLineEndPoint(start, end);
}

function constrainLineEndPoint(start: ChartPanePoint, end: ChartPanePoint): ChartPanePoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length === 0) {
    return end;
  }

  const snappedAngle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
  return {
    x: start.x + Math.cos(snappedAngle) * length,
    y: start.y + Math.sin(snappedAngle) * length,
  };
}

function constrainRectangleEndPoint(start: ChartPanePoint, end: ChartPanePoint): ChartPanePoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const size = Math.max(Math.abs(dx), Math.abs(dy));
  return {
    x: start.x + Math.sign(dx || 1) * size,
    y: start.y + Math.sign(dy || 1) * size,
  };
}

export function isMeaningfulDrawing(
  drawing: DrawingShape,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): boolean {
  const start = drawingPointToPanePoint(drawing.start, chart, candleSeries);
  const end = drawingPointToPanePoint(drawing.end, chart, candleSeries);
  if (!start || !end) {
    return false;
  }

  if (drawing.kind === "line") {
    return Math.hypot(end.x - start.x, end.y - start.y) >= MIN_DRAWING_SIZE_PX;
  }

  return Math.abs(end.x - start.x) >= MIN_DRAWING_SIZE_PX && Math.abs(end.y - start.y) >= MIN_DRAWING_SIZE_PX;
}

export function buildDrawingOverlayState(
  handles: DrawingChartHandles | null,
  drawings: DrawingShape[],
  draft: DrawingDraft | null,
  anchorPreview: DrawingAnchorPreview | null,
  selectedDrawingId: string | null,
  revision: number,
  livePricePoint: DrawingLivePricePoint | null,
): DrawingOverlayState {
  void revision;
  if (!handles) {
    return { width: 0, height: 0, items: [], anchor: null, livePriceLine: null };
  }

  const paneSize = handles.chart.paneSize();
  if (paneSize.width <= 0 || paneSize.height <= 0) {
    return { width: 0, height: 0, items: [], anchor: null, livePriceLine: null };
  }

  const items: RenderableDrawing[] = [];
  for (const drawing of drawings) {
    const item = toRenderableDrawing(drawing, handles, false, drawing.id === selectedDrawingId);
    if (item) {
      items.push(item);
    }
  }
  if (draft) {
    const item = toRenderableDrawing(draft, handles, true, false);
    if (item) {
      items.push(item);
    }
  }

  const anchor = anchorPreview ? drawingPointToPanePoint(anchorPreview.point, handles.chart, handles.candleSeries) : null;
  const livePriceLine = toRenderableLivePriceLine(livePricePoint, handles, paneSize);
  return { width: paneSize.width, height: paneSize.height, items, anchor, livePriceLine };
}

function toRenderableLivePriceLine(
  livePricePoint: DrawingLivePricePoint | null,
  handles: DrawingChartHandles,
  paneSize: { width: number; height: number },
): RenderableLivePriceLine | null {
  if (!livePricePoint || !Number.isFinite(livePricePoint.price)) {
    return null;
  }

  const time = toUtcTimestamp(livePricePoint.timestamp);
  if (time === null) {
    return null;
  }

  const x = handles.chart.timeScale().timeToCoordinate(time);
  const y = handles.candleSeries.priceToCoordinate(livePricePoint.price);
  if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  if (x < 0 || x >= paneSize.width - 1 || y < 0 || y > paneSize.height) {
    return null;
  }

  return {
    x1: x,
    x2: paneSize.width,
    y,
  };
}

function toRenderableDrawing(
  drawing: DrawingShape,
  handles: DrawingChartHandles,
  isDraft: boolean,
  isSelected: boolean,
): RenderableDrawing | null {
  const start = drawingPointToPanePoint(drawing.start, handles.chart, handles.candleSeries);
  const end = drawingPointToPanePoint(drawing.end, handles.chart, handles.candleSeries);
  if (!start || !end) {
    return null;
  }

  return {
    id: drawing.id,
    kind: drawing.kind,
    isDraft,
    isSelected,
    x1: start.x,
    y1: start.y,
    x2: end.x,
    y2: end.y,
  };
}

export function findDrawingHitTargetAtPanePoint(
  panePoint: ChartPanePoint,
  handles: DrawingChartHandles | null,
  drawings: DrawingShape[],
): DrawingHitTarget | null {
  if (!handles) {
    return null;
  }

  for (let index = drawings.length - 1; index >= 0; index -= 1) {
    const item = toRenderableDrawing(drawings[index], handles, false, false);
    if (!item) {
      continue;
    }

    if (Math.hypot(panePoint.x - item.x1, panePoint.y - item.y1) <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
      return { id: item.id, mode: "start" };
    }
    if (Math.hypot(panePoint.x - item.x2, panePoint.y - item.y2) <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
      return { id: item.id, mode: "end" };
    }
    const rectangleSideMode = findRectangleSideResizeMode(panePoint, item);
    if (rectangleSideMode) {
      return { id: item.id, mode: rectangleSideMode };
    }
    if (isPointOnRenderableDrawing(panePoint, item)) {
      return { id: item.id, mode: "body" };
    }
  }

  return null;
}

function findRectangleSideResizeMode(
  point: ChartPanePoint,
  item: RenderableDrawing,
): Extract<DrawingEditMode, "left" | "right"> | null {
  if (item.kind !== "rectangle") {
    return null;
  }

  const left = Math.min(item.x1, item.x2);
  const right = Math.max(item.x1, item.x2);
  const top = Math.min(item.y1, item.y2);
  const bottom = Math.max(item.y1, item.y2);
  const middleY = top + (bottom - top) / 2;
  const leftHandleDistance = Math.hypot(point.x - left, point.y - middleY);
  const rightHandleDistance = Math.hypot(point.x - right, point.y - middleY);

  if (leftHandleDistance <= DRAWING_ENDPOINT_HIT_RADIUS_PX || rightHandleDistance <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
    return leftHandleDistance <= rightHandleDistance ? "left" : "right";
  }

  if (point.y < top - RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX || point.y > bottom + RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX) {
    return null;
  }

  const leftEdgeDistance = Math.abs(point.x - left);
  const rightEdgeDistance = Math.abs(point.x - right);
  if (leftEdgeDistance <= RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX || rightEdgeDistance <= RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX) {
    return leftEdgeDistance <= rightEdgeDistance ? "left" : "right";
  }

  return null;
}

function isPointOnRenderableDrawing(point: ChartPanePoint, item: RenderableDrawing): boolean {
  if (item.kind === "line") {
    return distanceToLineSegment(point, { x: item.x1, y: item.y1 }, { x: item.x2, y: item.y2 }) <= DRAWING_HIT_RADIUS_PX;
  }

  const left = Math.min(item.x1, item.x2);
  const right = Math.max(item.x1, item.x2);
  const top = Math.min(item.y1, item.y2);
  const bottom = Math.max(item.y1, item.y2);
  const nearVerticalEdge =
    point.y >= top - DRAWING_HIT_RADIUS_PX &&
    point.y <= bottom + DRAWING_HIT_RADIUS_PX &&
    (Math.abs(point.x - left) <= DRAWING_HIT_RADIUS_PX || Math.abs(point.x - right) <= DRAWING_HIT_RADIUS_PX);
  const nearHorizontalEdge =
    point.x >= left - DRAWING_HIT_RADIUS_PX &&
    point.x <= right + DRAWING_HIT_RADIUS_PX &&
    (Math.abs(point.y - top) <= DRAWING_HIT_RADIUS_PX || Math.abs(point.y - bottom) <= DRAWING_HIT_RADIUS_PX);
  return nearVerticalEdge || nearHorizontalEdge;
}

function distanceToLineSegment(point: ChartPanePoint, start: ChartPanePoint, end: ChartPanePoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) {
    return Math.hypot(point.x - start.x, point.y - start.y);
  }

  const segmentPosition = clampNumber(((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared, 0, 1);
  const projectedX = start.x + segmentPosition * dx;
  const projectedY = start.y + segmentPosition * dy;
  return Math.hypot(point.x - projectedX, point.y - projectedY);
}

export function normalizeDrawingPoint(point: DrawingPoint): DrawingPoint {
  return {
    logical: point.logical,
    time: point.time,
    price: normalizeDraggedLiquidityPrice(point.price),
  };
}

export function isSameDrawingPoint(left: DrawingPoint, right: DrawingPoint): boolean {
  return Number(left.logical) === Number(right.logical) && left.price === right.price;
}

export function releaseChartPointerCapture(container: HTMLElement | null, pointerId: number) {
  if (!container) {
    return;
  }

  if (container.hasPointerCapture(pointerId)) {
    container.releasePointerCapture(pointerId);
  }

  const eventSurface = container.parentElement;
  if (eventSurface?.hasPointerCapture(pointerId)) {
    eventSurface.releasePointerCapture(pointerId);
  }
}

export function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function isEditableEventTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}

export function isChartOverlayControlEventTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest("[data-chart-overlay-control='true']") !== null;
}
