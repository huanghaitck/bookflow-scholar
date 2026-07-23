import {
  BookOpen,
  CircleHelp,
  Cpu,
  Minimize2,
  PanelTopClose,
  Search,
  Square,
  Wifi,
  X,
} from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { useEffect, useState } from 'react';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import type {
  BookflowSnapshot,
  AmbientMotionMode,
  FrontendPreferences,
  MascotCharacter,
  MascotForm,
  MascotSkin,
  ThemeId,
  UiLocale,
} from '../domain/bookflow-contract';
import { translate } from '../i18n/messages';
import { localizeDisplayValue } from '../i18n/status-codes';
import {
  CHARACTER_LABELS,
  LOCALE_LABELS,
} from '../state/frontend-preferences';
import { THEME_CONFIGS } from '../themes/theme-registry';
import { bookflowBridge } from '../state/useBookflowSnapshot';

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="shell-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function GlobalTopBar({
  preferences,
  setPreferences,
  snapshot,
}: {
  preferences: FrontendPreferences;
  setPreferences: Dispatch<SetStateAction<FrontendPreferences>>;
  snapshot: BookflowSnapshot;
}) {
  const [closeGuardOpen, setCloseGuardOpen] = useState(false);
  const [compact, setCompact] = useState(false);
  const t = (key: Parameters<typeof translate>[1]) =>
    translate(preferences.uiLocale, key);
  const backendSources = (snapshot.backendState?.sources ?? []) as Array<Record<string, unknown>>;
  const activeFilename = String(backendSources.at(-1)?.filename ?? '尚未导入来源');
  const hasActiveTask = snapshot.canPause || snapshot.canCancel || ['working', 'thinking'].includes(snapshot.workflowState);
  const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  const windowAction = async (action: 'minimize' | 'maximize_toggle' | 'compact_toggle' | 'start_dragging' | 'close') => {
    if (!inTauri) return;
    await invoke('bookflow_window_command', { action });
  };
  const requestClose = () => {
    if (hasActiveTask) setCloseGuardOpen(true);
    else void windowAction('close');
  };
  const toggleCompact = async () => {
    const nextCompact = !compact;
    try {
      await windowAction('compact_toggle');
    } catch {
      return;
    }
    document.documentElement.classList.toggle('bookflow-compact', nextCompact);
    setCompact(nextCompact);
  };
  const closeWithTaskAction = async (action: 'pause' | 'cancel') => {
    const result = await bookflowBridge.command({ type: action === 'pause' ? 'workflow.pause' : 'workflow.cancel' });
    if (!result.data.accepted) return;
    await windowAction('close');
  };

  useEffect(() => {
    if (!inTauri) return undefined;
    let unlisten: (() => void) | undefined;
    void listen('bookflow://close-requested', requestClose).then((dispose) => { unlisten = dispose; });
    return () => unlisten?.();
  }, [hasActiveTask, inTauri]);
  const setCharacter = (activeCharacter: MascotCharacter) => {
    setPreferences((current) => ({
      ...current,
      activeCharacter,
      activeSkin:
        activeCharacter === 'mascot_editor'
          ? current.activeSkin
          : 'skin_default',
    }));
  };
  const setMascotForm = (mascotForm: MascotForm) => {
    setPreferences((current) => {
      if (current.mascotForm === mascotForm) return current;
      const baselineOffset = mascotForm === 'full' ? -56 : 56;
      return {
        ...current,
        mascotForm,
        mascotPosition: {
          ...current.mascotPosition,
          y: Math.max(8, current.mascotPosition.y + baselineOffset),
        },
      };
    });
  };

  return (
    <header
      className="global-topbar"
      data-tauri-drag-region
      onPointerDown={(event) => {
        if (event.button === 0 && !(event.target as HTMLElement).closest('button,input,select,label,a')) {
          void windowAction('start_dragging');
        }
      }}
      onDoubleClick={(event) => {
        if (!compact && !(event.target as HTMLElement).closest('button,input,select,label')) void windowAction('maximize_toggle');
      }}
    >
      <div className="brand-lockup">
        <span className="brand-mark"><BookOpen size={21} /></span>
        <div>
          <strong>{t('productDesktop')}</strong>
          <small>{snapshot.workspaceName ?? t('workspace')} · {activeFilename} · {localizeDisplayValue(snapshot.currentStage, preferences.uiLocale)}</small>
        </div>
      </div>
      <label className="global-search">
        <Search size={14} />
        <input aria-label={t('searchPlaceholder')} placeholder={t('searchPlaceholder')} />
      </label>
      <div className="global-controls">
        <Field label={t('theme')}>
          <select
            data-testid="theme-select"
            value={preferences.theme}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                theme: event.target.value as ThemeId,
              }))
            }
          >
            {THEME_CONFIGS.map((theme) => (
              <option value={theme.id} key={theme.id}>
                {preferences.uiLocale === 'zh-Hans' ? theme.displayName : `${theme.displayName} · ${theme.label}`}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('character')}>
          <select
            data-testid="character-select"
            value={preferences.activeCharacter}
            onChange={(event) =>
              setCharacter(event.target.value as MascotCharacter)
            }
          >
            {CHARACTER_LABELS.map((character) => (
              <option value={character.id} key={character.id}>
                {character.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('skin')}>
          <select
            data-testid="skin-select"
            value={preferences.activeSkin}
            disabled={preferences.activeCharacter !== 'mascot_editor'}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                activeSkin: event.target.value as MascotSkin,
              }))
            }
          >
            <option value="skin_default">{t('defaultSkin')}</option>
            {preferences.activeCharacter === 'mascot_editor' && (
              <option value="skin_midnight_archivist">{t('midnightArchivist')}</option>
            )}
          </select>
        </Field>
        <div className="form-switch" aria-label={t('form')}>
          {(['full', 'chibi'] as const).map((form: MascotForm) => (
            <button
              type="button"
              key={form}
              data-testid={`toggle-form-${form}`}
              aria-pressed={preferences.mascotForm === form}
              onClick={() => setMascotForm(form)}
            >
              {t(form)}
            </button>
          ))}
        </div>
        <Field label={t('uiLanguage')}>
          <select
            data-testid="locale-select"
            value={preferences.uiLocale}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                uiLocale: event.target.value as UiLocale,
              }))
            }
          >
            {Object.entries(LOCALE_LABELS).map(([locale, label]) => (
              <option value={locale} key={locale}>{label}</option>
            ))}
          </select>
        </Field>
        <Field label={t('ambientMotion')}>
          <select
            className="ambient-motion-select"
            data-testid="ambient-motion-select"
            value={preferences.ambientMotion}
            onChange={(event) =>
              setPreferences((current) => ({
                ...current,
                ambientMotion: event.target.value as AmbientMotionMode,
              }))
            }
          >
            <option value="full">{t('ambientFull')}</option>
            <option value="reduced">{t('ambientReduced')}</option>
            <option value="off">{t('ambientOff')}</option>
          </select>
        </Field>
        <button
          type="button"
          className="topbar-icon-button"
          aria-label={t('help')}
          onClick={() =>
            setPreferences((current) => ({
              ...current,
              helpOpen: !current.helpOpen,
            }))
          }
        >
          <CircleHelp size={18} />
        </button>
        <div className="connection-pill" title={t('connection')}>
          <Wifi size={14} />
          <span>
            {snapshot.connectionState === 'connected'
              ? t('connected')
              : localizeDisplayValue(snapshot.connectionState, preferences.uiLocale)}
          </span>
        </div>
        <div className="provider-pill" title={t('providers')}>
          <Cpu size={14} />
          <span>T · {localizeDisplayValue(snapshot.providerStatus.text.status, preferences.uiLocale)}</span>
          <span>V · {localizeDisplayValue(snapshot.providerStatus.vlm.status, preferences.uiLocale)}</span>
        </div>
      </div>
      <div className="window-controls" aria-label={t('productDesktop')}>
        <button type="button" aria-label={t('minimize')} onClick={() => void windowAction('minimize')}><Minimize2 size={14} /></button>
        <button type="button" aria-label="紧凑模式" aria-pressed={compact} onClick={() => void toggleCompact()}><PanelTopClose size={13} /></button>
        <button type="button" aria-label={t('maximize')} disabled={compact} title={compact ? '请先退出紧凑模式' : undefined} onClick={() => void windowAction('maximize_toggle')}><Square size={12} /></button>
        <button type="button" aria-label={t('close')} onClick={requestClose}><X size={14} /></button>
      </div>
      {closeGuardOpen && (
        <div className="close-guard-backdrop" role="presentation">
          <section className="close-guard" role="dialog" aria-modal="true" aria-labelledby="close-guard-title">
            <h2 id="close-guard-title">任务仍在运行</h2>
            <p>退出前请选择如何处理当前任务。检查点和已有结果会保留。</p>
            <div>
              {snapshot.canPause && <button type="button" onClick={() => void closeWithTaskAction('pause')}>暂停任务并退出</button>}
              {snapshot.canCancel && <button type="button" onClick={() => void closeWithTaskAction('cancel')}>取消任务并退出</button>}
              <button type="button" onClick={() => setCloseGuardOpen(false)}>返回</button>
            </div>
          </section>
        </div>
      )}
    </header>
  );
}
