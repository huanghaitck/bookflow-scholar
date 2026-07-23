import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEventHandler,
  type RefObject,
} from 'react';
import type { MascotPosition } from '../domain/bookflow-contract';

export type MascotDragPhase =
  | 'idle'
  | 'drag-start'
  | 'dragging'
  | 'drag-end'
  | 'drag-cancel';

interface DragContext {
  pointerId: number;
  offsetX: number;
  offsetY: number;
  startX: number;
  startY: number;
  origin: MascotPosition;
  active: boolean;
}

interface UseMascotDragOptions {
  position: MascotPosition;
  setPosition: (position: MascotPosition) => void;
  overlayRef: RefObject<HTMLDivElement | null>;
  hostRef: RefObject<HTMLElement | null>;
  persistenceKey: string | null;
  onDragStart?: (position: MascotPosition) => void;
  onDragMove?: (position: MascotPosition) => void;
  onDragEnd?: (position: MascotPosition) => void;
  onDragCancel?: (position: MascotPosition) => void;
}

function emitDragLifecycle(phase: MascotDragPhase, position: MascotPosition) {
  window.dispatchEvent(new CustomEvent('bookflow:mascot-drag', {
    detail: { phase, position, renderer: 'code-driven-2d' },
  }));
}

export function clampMascotPosition(
  position: MascotPosition,
  overlay: DOMRect,
  host: DOMRect,
): MascotPosition {
  const maxX = Math.max(0, overlay.width - host.width);
  const maxY = Math.max(0, overlay.height - host.height);
  return {
    x: Math.min(Math.max(position.x, 0), maxX),
    y: Math.min(Math.max(position.y, 0), maxY),
    dock: position.dock,
  };
}

export function useMascotDrag({
  position,
  setPosition,
  overlayRef,
  hostRef,
  persistenceKey,
  onDragStart,
  onDragMove,
  onDragEnd,
  onDragCancel,
}: UseMascotDragOptions) {
  const [phase, setPhase] = useState<MascotDragPhase>('idle');
  const drag = useRef<DragContext | null>(null);
  const suppressClick = useRef(false);
  const settleTimer = useRef<number | null>(null);
  const latestPosition = useRef(position);
  latestPosition.current = position;

  const clamp = useCallback((candidate: MascotPosition) => {
    const overlay = overlayRef.current?.getBoundingClientRect();
    const host = hostRef.current?.getBoundingClientRect();
    if (!overlay || !host) return candidate;
    return clampMascotPosition(candidate, overlay, host);
  }, [hostRef, overlayRef]);

  useLayoutEffect(() => {
    const next = clamp(position);
    if (next.x !== position.x || next.y !== position.y) setPosition(next);
  });

  useEffect(() => {
    if (!persistenceKey) return;
    localStorage.setItem(persistenceKey, JSON.stringify(position));
  }, [persistenceKey, position]);

  useEffect(() => {
    const onResize = () => setPosition(clamp(latestPosition.current));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [clamp, setPosition]);

  useEffect(() => () => {
    if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
  }, []);

  const scheduleIdle = () => {
    if (settleTimer.current !== null) window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => {
      settleTimer.current = null;
      setPhase('idle');
    }, 120);
  };

  const onPointerDown: PointerEventHandler<HTMLDivElement> = (event) => {
    if (event.button !== 0) return;
    const host = hostRef.current?.getBoundingClientRect();
    const overlay = overlayRef.current?.getBoundingClientRect();
    if (!host || !overlay) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - host.left,
      offsetY: event.clientY - host.top,
      startX: event.clientX,
      startY: event.clientY,
      origin: latestPosition.current,
      active: false,
    };
  };

  const onPointerMove: PointerEventHandler<HTMLDivElement> = (event) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const overlay = overlayRef.current?.getBoundingClientRect();
    if (!overlay) return;
    if (!drag.current.active) {
      const distance = Math.hypot(
        event.clientX - drag.current.startX,
        event.clientY - drag.current.startY,
      );
      if (distance < 5) return;
      drag.current.active = true;
      suppressClick.current = true;
      setPhase('drag-start');
      emitDragLifecycle('drag-start', latestPosition.current);
      onDragStart?.(latestPosition.current);
    }
    const next = clamp({
      x: event.clientX - overlay.left - drag.current.offsetX,
      y: event.clientY - overlay.top - drag.current.offsetY,
      dock: 'free',
    });
    latestPosition.current = next;
    setPosition(next);
    setPhase('dragging');
    emitDragLifecycle('dragging', next);
    onDragMove?.(next);
  };

  const finishDrag: PointerEventHandler<HTMLDivElement> = (event) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const wasActive = drag.current.active;
    const overlay = overlayRef.current?.getBoundingClientRect();
    const host = hostRef.current?.getBoundingClientRect();
    let next = clamp(latestPosition.current);
    if (overlay && host) {
      const maxX = Math.max(0, overlay.width - host.width);
      if (next.x <= 16) next = { ...next, x: 0, dock: 'left' };
      else if (maxX - next.x <= 16) next = { ...next, x: maxX, dock: 'right' };
    }
    drag.current = null;
    if (!wasActive) return;
    latestPosition.current = next;
    setPosition(next);
    setPhase('drag-end');
    emitDragLifecycle('drag-end', next);
    onDragEnd?.(next);
    scheduleIdle();
  };

  const cancelDrag: PointerEventHandler<HTMLDivElement> = (event) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const origin = clamp(drag.current.origin);
    const wasActive = drag.current.active;
    drag.current = null;
    if (!wasActive) return;
    latestPosition.current = origin;
    setPosition(origin);
    setPhase('drag-cancel');
    emitDragLifecycle('drag-cancel', origin);
    onDragCancel?.(origin);
    scheduleIdle();
  };

  return {
    phase,
    onPointerDown,
    onPointerMove,
    onPointerUp: finishDrag,
    onPointerCancel: cancelDrag,
    shouldSuppressClick: () => {
      const shouldSuppress = suppressClick.current;
      suppressClick.current = false;
      return shouldSuppress;
    },
  };
}
