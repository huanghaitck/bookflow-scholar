import { Bell } from 'lucide-react';
import type { UiLocale } from '../domain/bookflow-contract';
import { translate } from '../i18n/messages';
import { NAVIGATION, type RouteId } from './navigation';

export function GlobalNavigation({
  route,
  setRoute,
  locale,
  reviewQueueCount,
}: {
  route: RouteId;
  setRoute: (route: RouteId) => void;
  locale: UiLocale;
  reviewQueueCount: number;
}) {
  return (
    <nav className="global-navigation" aria-label="Primary">
      {NAVIGATION.map((item) => {
        const Icon = item.icon;
        return (
          <button
            type="button"
            key={item.id}
            data-testid={`nav-${item.id}`}
            aria-current={route === item.id ? 'page' : undefined}
            onClick={() => setRoute(item.id)}
          >
            <Icon size={18} />
            <span>{translate(locale, item.label)}</span>
            {item.id === 'comparison' && reviewQueueCount > 0 && (
              <em>{reviewQueueCount}</em>
            )}
          </button>
        );
      })}
      <div className="navigation-status">
        <Bell size={15} />
        <span>{translate(locale, 'technicalCandidate')}</span>
      </div>
    </nav>
  );
}
