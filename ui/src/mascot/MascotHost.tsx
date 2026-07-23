import {
  Eye,
  EyeOff,
  Grip,
  ImageOff,
  RotateCcw,
} from 'lucide-react';
import { DotLottieReact } from '@lottiefiles/dotlottie-react';
import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import type {
  BookflowSnapshot,
  FrontendPreferences,
  MascotPosition,
} from '../domain/bookflow-contract';
import { derivePresentationState } from '../adapters/derivePresentationState';
import { translate } from '../i18n/messages';
import { useMascotDrag } from './useMascotDrag';
import { getThemeConfig } from '../themes/theme-registry';
import { mascotRuntimeAdapter } from './runtime/MascotRuntimeAdapter';

export interface MascotInteractionHooks {
  onDragStart?: (position: MascotPosition) => void;
  onDragMove?: (position: MascotPosition) => void;
  onDragEnd?: (position: MascotPosition) => void;
  onDragCancel?: (position: MascotPosition) => void;
  onClick?: () => void;
  onDoubleClick?: () => void;
  onStateChange?: (state: BookflowSnapshot['mascotState']) => void;
  onThemeChange?: (theme: FrontendPreferences['theme']) => void;
  onFallback?: (reason: 'state-asset' | 'idle-asset') => void;
}

const EMPTY_HOOKS: MascotInteractionHooks = {};

interface MascotHostProps {
  snapshot: BookflowSnapshot;
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
  persistenceKey: string | null;
  hooks?: MascotInteractionHooks;
}

const STATE_BUBBLES: Record<BookflowSnapshot['mascotState'], string> = {
  idle: 'Ready when you are.',
  thinking: 'Let me think…',
  working: 'Working on it ✦',
  reviewing: 'Reviewing carefully.',
  completed: 'All done!',
  warning: 'This needs attention.',
  error: 'I found a problem.',
  sleeping: 'Resting quietly…',
};

export function MascotHost({
  snapshot,
  preferences,
  setPreferences,
  persistenceKey,
  hooks = EMPTY_HOOKS,
}: MascotHostProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLElement>(null);
  const instanceId = useRef(
    globalThis.crypto?.randomUUID?.() ?? `mascot-${Date.now()}`,
  );
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
  const [interaction, setInteraction] = useState<'idle' | 'click' | 'double-click'>('idle');

  useEffect(() => {
    setDisplayUrl(requestedUrl);
    setAssetUnavailable(false);
  }, [requestedUrl]);

  useEffect(() => {
    hooks.onStateChange?.(presentationState);
  }, [hooks, presentationState]);

  useEffect(() => {
    hooks.onThemeChange?.(preferences.theme);
  }, [hooks, preferences.theme]);

  useEffect(() => () => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
  }, []);

  const triggerInteraction = (next: 'click' | 'double-click') => {
    if (interactionTimer.current !== null) window.clearTimeout(interactionTimer.current);
    setInteraction(next);
    interactionTimer.current = window.setTimeout(() => {
      interactionTimer.current = null;
      setInteraction('idle');
    }, next === 'double-click' ? 520 : 260);
  };

  const setPosition = (mascotPosition: MascotPosition) => {
    setPreferences((current) => ({ ...current, mascotPosition }));
  };
  const drag = useMascotDrag({
    position: preferences.mascotPosition,
    setPosition,
    overlayRef,
    hostRef,
    persistenceKey,
    onDragStart: hooks.onDragStart,
    onDragMove: hooks.onDragMove,
    onDragEnd: hooks.onDragEnd,
    onDragCancel: hooks.onDragCancel,
  });
  const resetPosition = () => {
    setPosition(getThemeConfig(preferences.theme).defaultMascotPosition);
  };

  return (
    <div
      ref={overlayRef}
      className="mascot-overlay-root"
      data-testid="mascot-overlay-root"
      data-theme-anchor={preferences.theme}
      aria-label="Global mascot overlay"
    >
      {!preferences.mascotVisible ? (
        <button
          type="button"
          className="mascot-restore"
          data-testid="toggle-mascot"
          onClick={() =>
            setPreferences((current) => ({ ...current, mascotVisible: true }))
          }
        >
          <Eye size={16} />
          {translate(preferences.uiLocale, 'showMascot')}
        </button>
      ) : (
        <aside
          ref={hostRef}
          className={`mascot-host form-${preferences.mascotForm}`}
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
          style={{
            transform: `translate3d(${preferences.mascotPosition.x}px, ${preferences.mascotPosition.y}px, 0)`,
          }}
          aria-label={translate(preferences.uiLocale, 'mascot')}
        >
          <div className="mascot-control-pill">
            <Grip size={14} aria-hidden="true" />
            <span>{presentationState}</span>
            <button
              type="button"
              data-testid="reset-mascot-position"
              aria-label="Reset mascot position"
              onClick={resetPosition}
            >
              <RotateCcw size={13} />
            </button>
            <button
              type="button"
              data-testid="toggle-mascot"
              aria-label={translate(preferences.uiLocale, 'hideMascot')}
              onClick={() =>
                setPreferences((current) => ({ ...current, mascotVisible: false }))
              }
            >
              <EyeOff size={14} />
            </button>
          </div>
          <div
            className="mascot-drag-surface"
            data-testid="mascot-drag-handle"
            role="button"
            tabIndex={0}
            aria-label="Drag mascot across the application window"
            onPointerDown={drag.onPointerDown}
            onPointerMove={drag.onPointerMove}
            onPointerUp={drag.onPointerUp}
            onPointerCancel={drag.onPointerCancel}
            onClick={() => {
              if (!drag.shouldSuppressClick()) {
                triggerInteraction('click');
                hooks.onClick?.();
              }
            }}
            onDoubleClick={() => {
              triggerInteraction('double-click');
              hooks.onDoubleClick?.();
            }}
          >
            <span className="mascot-speech">{STATE_BUBBLES[presentationState]}</span>
            <span className="mascot-effect-layer" aria-hidden="true">
              <DotLottieReact
                autoplay={presentation.motion === 'full'}
                loop={presentation.motion === 'full'}
                src={presentation.effectUrl}
              />
            </span>
            {assetUnavailable ? (
              <div className="mascot-asset-error" role="status">
                <ImageOff size={24} />
                Static mascot unavailable
              </div>
            ) : (
              <img
                src={displayUrl}
                data-testid="mascot-image"
                draggable={false}
                alt={`${preferences.activeCharacter}, ${preferences.mascotForm}, ${presentationState}`}
                onError={() => {
                  if (displayUrl !== idleUrl) {
                    hooks.onFallback?.('state-asset');
                    setDisplayUrl(idleUrl);
                  } else {
                    hooks.onFallback?.('idle-asset');
                    setAssetUnavailable(true);
                  }
                }}
              />
            )}
            <small>Drag me around · local 2D runtime</small>
          </div>
        </aside>
      )}
    </div>
  );
}
