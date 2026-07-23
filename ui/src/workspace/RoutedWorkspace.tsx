import type { Dispatch, SetStateAction } from 'react';
import type {
  BookflowSnapshot,
  FrontendPreferences,
} from '../domain/bookflow-contract';
import { translate } from '../i18n/messages';
import { localizeStatusCode } from '../i18n/status-codes';
import { NAVIGATION, type RouteId } from '../shell/navigation';
import { getThemeConfig } from '../themes/theme-registry';
import { OverviewWorkspace } from './OverviewWorkspace';
import { BackendDrivenWorkspace } from './BackendDrivenWorkspace';

export function RoutedWorkspace({
  route,
  snapshot,
  preferences,
  setPreferences,
}: {
  route: RouteId;
  snapshot: BookflowSnapshot;
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
}) {
  const item = NAVIGATION.find((candidate) => candidate.id === route)!;
  const theme = getThemeConfig(preferences.theme);
  const localizedProblem = localizeStatusCode(
    snapshot.errorCode ?? snapshot.warningCode,
    preferences.uiLocale,
  );

  return (
    <main className="routed-workspace">
      {localizedProblem && <div className="problem-banner">{localizedProblem}</div>}
      {route === 'overview' ? (
        <div
          className="theme-workbench"
          data-testid="theme-layout"
          data-layout={theme.layoutId}
          data-material={preferences.theme}
          aria-label={theme.displayName}
        >
          <OverviewWorkspace
            snapshot={snapshot}
            preferences={preferences}
            setPreferences={setPreferences}
          />
        </div>
      ) : <BackendDrivenWorkspace route={route} snapshot={snapshot} preferences={preferences} />}
      {route === 'overview' && (
        <h1 className="visually-hidden" data-testid="route-title">
          {translate(preferences.uiLocale, item.label)}
        </h1>
      )}
    </main>
  );
}
