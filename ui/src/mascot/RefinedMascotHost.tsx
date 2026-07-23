import { DotLottieReact } from '@lottiefiles/dotlottie-react';
import { Eye, EyeOff, Grip, ImageOff, RotateCcw, Scaling } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { derivePresentationState } from '../adapters/derivePresentationState';
import type { BookflowSnapshot, FrontendPreferences, MascotPosition } from '../domain/bookflow-contract';
import { translate } from '../i18n/messages';
import { localizeDisplayValue } from '../i18n/status-codes';
import { getThemeConfig } from '../themes/theme-registry';
import { useMascotDrag } from './useMascotDrag';
import { mascotRuntimeAdapter } from './runtime/MascotRuntimeAdapter';

interface RefinedMascotHostProps {
  snapshot: BookflowSnapshot;
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
  persistenceKey: string | null;
}

const STATE_BUBBLES: Record<BookflowSnapshot['mascotState'], { 'zh-Hans': string; en: string }> = {
  idle: { 'zh-Hans': '准备好了，随时可以开始。', en: 'Ready when you are.' },
  thinking: { 'zh-Hans': '让我仔细想一想……', en: 'Let me think…' },
  working: { 'zh-Hans': '正在认真处理 ✦', en: 'Working on it ✦' },
  reviewing: { 'zh-Hans': '正在逐项复核。', en: 'Reviewing carefully.' },
  completed: { 'zh-Hans': '全部完成啦！', en: 'All done!' },
  warning: { 'zh-Hans': '这里需要留意。', en: 'This needs attention.' },
  error: { 'zh-Hans': '发现了一个问题。', en: 'I found a problem.' },
  sleeping: { 'zh-Hans': '安静休息一会儿……', en: 'Resting quietly…' },
};

export function RefinedMascotHost({
  snapshot,
  preferences,
  setPreferences,
  persistenceKey,
}: RefinedMascotHostProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const positionLayerRef = useRef<HTMLElement>(null);
  const scaleLayerRef = useRef<HTMLDivElement>(null);
  const scaleInputRef = useRef<HTMLInputElement>(null);
  const scaleOutputRef = useRef<HTMLOutputElement>(null);
  const scaleFrameRef = useRef<number | null>(null);
  const boundaryFrameRef = useRef<number | null>(null);
  const transientScaleRef = useRef(preferences.mascotScale);
  const instanceId = useRef(globalThis.crypto?.randomUUID?.() ?? `mascot-${Date.now()}`);
  const interactionTimer = useRef<number | null>(null);
  const presentationState = derivePresentationState(snapshot.workflowState);
  const presentation = mascotRuntimeAdapter.resolve({
    character: preferences.activeCharacter,
    skin: preferences.activeSkin,
    form: preferences.mascotForm,
    state: presentationState,
    ambientMotion: preferences.ambientMotion,
    reducedMotion: preferences.reducedMotion,
  });
  const requestedUrl = presentation.requestedUrl;
  const idleUrl = presentation.idleFallbackUrl;
  const [displayUrl, setDisplayUrl] = useState(requestedUrl);
  const [assetUnavailable, setAssetUnavailable] = useState(false);
  const [bubbleVisible, setBubbleVisible] = useState(true);
  const [scaleControlsOpen, setScaleControlsOpen] = useState(false);
  const [interaction, setInteraction] = useState<'idle' | 'click' | 'double-click'>('idle');

  useEffect(() => {
    setDisplayUrl(requestedUrl);
    setAssetUnavailable(false);
  }, [requestedUrl]);

  useEffect(() => () => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
    if (scaleFrameRef.current !== null) window.cancelAnimationFrame(scaleFrameRef.current);
    if (boundaryFrameRef.current !== null) window.cancelAnimationFrame(boundaryFrameRef.current);
  }, []);

  const triggerInteraction = (next: 'click' | 'double-click') => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
    setInteraction(next);
    interactionTimer.current = window.setTimeout(() => {
      interactionTimer.current = null;
      setInteraction('idle');
    }, next === 'double-click' ? 520 : 260);
  };
  const setPosition = useCallback((mascotPosition: MascotPosition) => {
    setPreferences((current) => ({ ...current, mascotPosition }));
  }, [setPreferences]);

  const correctVisualBoundary = useCallback(() => {
    if (boundaryFrameRef.current !== null) return;
    boundaryFrameRef.current = window.requestAnimationFrame(() => {
      boundaryFrameRef.current = null;
      const overlay = overlayRef.current?.getBoundingClientRect();
      const visual = scaleLayerRef.current?.getBoundingClientRect();
      if (!overlay || !visual) return;
      let deltaX = 0;
      let deltaY = 0;
      if (visual.left < overlay.left) deltaX += overlay.left - visual.left;
      if (visual.right > overlay.right) deltaX -= visual.right - overlay.right;
      if (visual.top < overlay.top) deltaY += overlay.top - visual.top;
      if (visual.bottom > overlay.bottom) deltaY -= visual.bottom - overlay.bottom;
      if (Math.abs(deltaX) < 0.5 && Math.abs(deltaY) < 0.5) return;
      setPreferences((current) => ({
        ...current,
        mascotPosition: {
          ...current.mascotPosition,
          x: Math.max(0, current.mascotPosition.x + deltaX),
          y: Math.max(0, current.mascotPosition.y + deltaY),
        },
      }));
    });
  }, [setPreferences]);

  const applyTransientScale = useCallback((nextScale: number) => {
    const bounded = Math.min(160, Math.max(60, Math.round(nextScale / 5) * 5));
    transientScaleRef.current = bounded;
    scaleLayerRef.current?.style.setProperty('--mascot-user-scale', String(bounded / 100));
    positionLayerRef.current?.setAttribute('data-scale', String(bounded));
    if (scaleInputRef.current && scaleInputRef.current.value !== String(bounded)) {
      scaleInputRef.current.value = String(bounded);
    }
    if (scaleOutputRef.current) scaleOutputRef.current.textContent = `${bounded}%`;
  }, []);

  const flushTransientScale = useCallback(() => {
    if (scaleFrameRef.current !== null) {
      window.cancelAnimationFrame(scaleFrameRef.current);
      scaleFrameRef.current = null;
    }
    applyTransientScale(transientScaleRef.current);
  }, [applyTransientScale]);

  const scheduleTransientScale = useCallback((nextScale: number) => {
    transientScaleRef.current = nextScale;
    if (scaleFrameRef.current !== null) return;
    scaleFrameRef.current = window.requestAnimationFrame(() => {
      scaleFrameRef.current = null;
      applyTransientScale(transientScaleRef.current);
    });
  }, [applyTransientScale]);

  const commitTransientScale = useCallback(() => {
    flushTransientScale();
    scaleLayerRef.current?.setAttribute('data-scaling', 'false');
    const nextScale = transientScaleRef.current;
    setPreferences((current) => current.mascotScale === nextScale
      ? current
      : { ...current, mascotScale: nextScale });
    correctVisualBoundary();
  }, [correctVisualBoundary, flushTransientScale, setPreferences]);

  const cancelTransientScale = useCallback(() => {
    scaleLayerRef.current?.setAttribute('data-scaling', 'false');
    applyTransientScale(preferences.mascotScale);
  }, [applyTransientScale, preferences.mascotScale]);

  useLayoutEffect(() => {
    transientScaleRef.current = preferences.mascotScale;
    applyTransientScale(preferences.mascotScale);
  }, [applyTransientScale, preferences.mascotScale]);

  useEffect(() => {
    const input = scaleInputRef.current;
    if (!input) return;
    input.addEventListener('change', commitTransientScale);
    return () => input.removeEventListener('change', commitTransientScale);
  }, [commitTransientScale, scaleControlsOpen]);

  const drag = useMascotDrag({
    position: preferences.mascotPosition,
    setPosition,
    overlayRef,
    hostRef: scaleLayerRef,
    persistenceKey,
    onDragEnd: correctVisualBoundary,
  });
  const resetPosition = () => {
    const defaultPosition = getThemeConfig(preferences.theme).defaultMascotPosition;
    setPosition({
      ...defaultPosition,
      y: preferences.mascotForm === 'full' ? Math.max(8, defaultPosition.y - 56) : defaultPosition.y,
    });
    correctVisualBoundary();
  };
  const scaleLabel = preferences.uiLocale === 'zh-Hans'
    ? '调整桌宠缩放'
    : 'Adjust mascot scale';
  const resetScaleLabel = preferences.uiLocale === 'zh-Hans'
    ? '恢复桌宠默认大小'
    : 'Reset mascot size';

  return (
    <div
      ref={overlayRef}
      className="mascot-overlay-root h2a-mascot-overlay"
      data-testid="mascot-overlay-root"
      data-layer-index="7"
      data-theme-anchor={preferences.theme}
      aria-label={translate(preferences.uiLocale, 'mascot')}
    >
      {!preferences.mascotVisible ? (
        <button
          type="button"
          className="mascot-restore"
          data-testid="toggle-mascot"
          onClick={() => setPreferences((current) => ({ ...current, mascotVisible: true }))}
        >
          <Eye size={16} />
          {translate(preferences.uiLocale, 'showMascot')}
        </button>
      ) : (
        <aside
          ref={positionLayerRef}
          className={`mascot-host h2a-mascot-host refined-mascot-position-layer form-${preferences.mascotForm}`}
          data-testid="mascot-host"
          data-host-instance={instanceId.current}
          data-renderer="code-driven-2d"
          data-fallback-entry="static-png"
          data-state={presentationState}
          data-effect={presentation.effectId}
          data-motion={presentation.motion}
          data-interaction={interaction}
          data-drag-state={drag.phase}
          data-dock={preferences.mascotPosition.dock}
          data-scale={preferences.mascotScale}
          style={{
            transform: `translate3d(${preferences.mascotPosition.x}px, ${preferences.mascotPosition.y}px, 0)`,
          }}
          aria-label={translate(preferences.uiLocale, 'mascot')}
        >
          <div className="h2a-mascot-status-row">
            <span className="h2a-mascot-status-tag" data-testid="mascot-status-tag">
              <Grip size={11} aria-hidden="true" />
              {localizeDisplayValue(presentationState, preferences.uiLocale)}
            </span>
            <div className="h2a-mascot-actions">
              <button
                type="button"
                data-testid="toggle-mascot-scale-controls"
                aria-label={scaleLabel}
                title={scaleLabel}
                aria-expanded={scaleControlsOpen}
                onClick={() => setScaleControlsOpen((open) => !open)}
              >
                <Scaling size={11} aria-hidden="true" />
              </button>
              <button type="button" data-testid="reset-mascot-position" aria-label={translate(preferences.uiLocale, 'resetMascot')} onClick={resetPosition}>
                <RotateCcw size={12} />
              </button>
              <button type="button" data-testid="toggle-mascot" aria-label={translate(preferences.uiLocale, 'hideMascot')} onClick={() => setPreferences((current) => ({ ...current, mascotVisible: false }))}>
                <EyeOff size={13} />
              </button>
            </div>
            {scaleControlsOpen && (
              <div className="h3-mascot-scale-menu" data-testid="mascot-scale-controls">
                <label title={scaleLabel}>
                  <Scaling size={11} aria-hidden="true" />
                <input
                  ref={scaleInputRef}
                  type="range"
                  min="60"
                  max="160"
                  step="5"
                  defaultValue={preferences.mascotScale}
                  aria-label={scaleLabel}
                  data-testid="mascot-scale"
                  onInput={(event) => scheduleTransientScale(Number(event.currentTarget.value))}
                  onPointerDown={() => scaleLayerRef.current?.setAttribute('data-scaling', 'true')}
                  onPointerUp={commitTransientScale}
                  onPointerCancel={cancelTransientScale}
                  onKeyUp={commitTransientScale}
                  onBlur={commitTransientScale}
                />
                <output ref={scaleOutputRef}>{preferences.mascotScale}%</output>
                </label>
                <button
                  type="button"
                  data-testid="reset-mascot-scale"
                  aria-label={resetScaleLabel}
                  title={resetScaleLabel}
                  onClick={() => {
                    scaleLayerRef.current?.setAttribute('data-scaling', 'false');
                    applyTransientScale(100);
                    commitTransientScale();
                  }}
                >
                  <RotateCcw size={11} />
                </button>
              </div>
            )}
          </div>
          <div
            ref={scaleLayerRef}
            className="refined-mascot-scale-layer"
            data-testid="mascot-scale-layer"
            data-scaling="false"
            style={{ '--mascot-user-scale': preferences.mascotScale / 100 } as CSSProperties}
          >
            <div className="frozen-mascot-runtime-animation-layer" data-testid="mascot-runtime-animation-layer">
              <div
                className="mascot-drag-surface h2a-mascot-drag-surface"
                data-testid="mascot-drag-handle"
                role="button"
                tabIndex={0}
                aria-label={translate(preferences.uiLocale, 'dragMascot')}
                onPointerDown={drag.onPointerDown}
                onPointerMove={drag.onPointerMove}
                onPointerUp={drag.onPointerUp}
                onPointerCancel={drag.onPointerCancel}
                onClick={() => {
                  if (!drag.shouldSuppressClick()) triggerInteraction('click');
                }}
                onDoubleClick={() => {
                  triggerInteraction('double-click');
                  setBubbleVisible((visible) => !visible);
                }}
              >
                {bubbleVisible && (
                  <span className="mascot-speech h2a-mascot-speech" data-testid="mascot-speech">
                    {STATE_BUBBLES[presentationState][preferences.uiLocale === 'zh-Hans' ? 'zh-Hans' : 'en']}
                  </span>
                )}
                <span className="mascot-effect-layer" aria-hidden="true">
                  <DotLottieReact autoplay={presentation.motion === 'full'} loop={presentation.motion === 'full'} src={presentation.effectUrl} />
                </span>
                {assetUnavailable ? (
                  <div className="mascot-asset-error" role="status">
                    <ImageOff size={24} />
                    {translate(preferences.uiLocale, 'staticMascotUnavailable')}
                  </div>
                ) : (
                  <img
                    src={displayUrl}
                    data-testid="mascot-image"
                    draggable={false}
                    alt={`${preferences.activeCharacter}, ${preferences.mascotForm}, ${presentationState}`}
                    onError={() => {
                      if (displayUrl !== idleUrl) setDisplayUrl(idleUrl);
                      else setAssetUnavailable(true);
                    }}
                  />
                )}
              </div>
            </div>
          </div>
        </aside>
      )}
    </div>
  );
}
