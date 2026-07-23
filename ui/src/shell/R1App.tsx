import { useEffect, useMemo, useState } from 'react';
import type {
  BookflowSnapshot,
  FrontendPreferences,
} from '../domain/bookflow-contract';
import { RefinedMascotHost } from '../mascot/RefinedMascotHost';
import {
  createInitialPreferences,
  FRONTEND_DISPLAY_PREFERENCES_KEY,
  safeStoredDisplayPreferences,
  safeStoredMascotPosition,
} from '../state/frontend-preferences';
import { useBookflowSnapshot } from '../state/useBookflowSnapshot';
import { getThemeConfig } from '../themes/theme-registry';
import { translate } from '../i18n/messages';
import { ThemeEnvironment } from '../themes/ThemeEnvironment';
import { RoutedWorkspace } from '../workspace/RoutedWorkspace';
import { GlobalHelpLayer } from './GlobalHelpLayer';
import { GlobalNavigation } from './GlobalNavigation';
import { GlobalTopBar } from './GlobalTopBar';
import type { RouteId } from './navigation';

export interface R1AppProps {
  initialPreferences?: Partial<FrontendPreferences>;
  initialRoute?: RouteId;
  snapshotOverride?: BookflowSnapshot;
  mascotPersistenceKey?: string | null;
}

const DEFAULT_MASCOT_PERSISTENCE_KEY = 'bookflow.mascot.position.v1';

export function R1App({
  initialPreferences = {},
  initialRoute = 'overview',
  snapshotOverride,
  mascotPersistenceKey = DEFAULT_MASCOT_PERSISTENCE_KEY,
}: R1AppProps) {
  const snapshot = useBookflowSnapshot(snapshotOverride);
  const [route, setRoute] = useState<RouteId>(initialRoute);
  const [preferences, setPreferences] = useState<FrontendPreferences>(() => {
    const storedDisplay = typeof localStorage === 'undefined'
      ? {}
      : safeStoredDisplayPreferences(
          localStorage.getItem(FRONTEND_DISPLAY_PREFERENCES_KEY),
        );
    const initial = createInitialPreferences({
      ...storedDisplay,
      ...initialPreferences,
    });
    if (!mascotPersistenceKey || typeof localStorage === 'undefined') return initial;
    const stored = safeStoredMascotPosition(
      localStorage.getItem(mascotPersistenceKey),
    );
    return stored ? { ...initial, mascotPosition: stored } : initial;
  });
  const theme = useMemo(
    () => getThemeConfig(preferences.theme),
    [preferences.theme],
  );

  useEffect(() => {
    localStorage.setItem(
      FRONTEND_DISPLAY_PREFERENCES_KEY,
      JSON.stringify({
        mascotScale: preferences.mascotScale,
        previewFontScale: preferences.previewFontScale,
        previewZoom: preferences.previewZoom,
      }),
    );
  }, [
    preferences.mascotScale,
    preferences.previewFontScale,
    preferences.previewZoom,
  ]);

  return (
    <div className="desktop-window">
      <div
        className="app-shell"
        data-theme={preferences.theme}
        data-theme-layout={theme.layoutId}
        data-layer-index="0"
        data-testid="app-shell"
      >
        <ThemeEnvironment
          theme={preferences.theme}
          motion={preferences.ambientMotion}
          reducedMotion={preferences.reducedMotion}
        />
        <GlobalTopBar
          preferences={preferences}
          setPreferences={setPreferences}
          snapshot={snapshot}
        />
        <GlobalNavigation
          route={route}
          setRoute={setRoute}
          locale={preferences.uiLocale}
          reviewQueueCount={snapshot.reviewQueueCount}
        />
        <div className="business-ui-layer" data-layer-index="6">
          <RoutedWorkspace
            route={route}
            snapshot={snapshot}
            preferences={preferences}
            setPreferences={setPreferences}
          />
        </div>
        <RefinedMascotHost
          snapshot={snapshot}
          preferences={preferences}
          setPreferences={setPreferences}
          persistenceKey={mascotPersistenceKey}
        />
        <GlobalHelpLayer
          preferences={preferences}
          setPreferences={setPreferences}
        />
        <footer className="desktop-statusbar">
          <span>v0.8.0-rc.2</span>
          <i />
          <span>{snapshot.connectionState === 'connected' ? '后端已连接' : '后端未连接'}</span>
          <span>{translate(preferences.uiLocale, 'autoSaveEnabled')}</span>
          <strong>{preferences.uiLocale === 'zh-Hans' ? theme.displayName : `${theme.displayName} · ${theme.label}`}</strong>
        </footer>
      </div>
    </div>
  );
}
