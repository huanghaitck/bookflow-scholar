import type {
  FrontendPreferences,
  MascotCharacter,
  MascotPosition,
  ThemeId,
  UiLocale,
} from '../domain/bookflow-contract';
import { getThemeConfig } from '../themes/theme-registry';

export const CHARACTER_LABELS: ReadonlyArray<{
  id: MascotCharacter;
  label: string;
}> = [
  { id: 'mascot_editor', label: 'Eleanor / 岚音' },
  { id: 'mascot_scholar', label: 'Clara / 清棠' },
  { id: 'mascot_explorer', label: 'Stella / 遥星' },
];

export const LOCALE_LABELS: Record<UiLocale, string> = {
  'zh-Hans': '简体中文',
  en: 'English',
  fr: 'Français',
  de: 'Deutsch',
  ja: '日本語',
  es: 'Español',
};

export const FRONTEND_DISPLAY_PREFERENCES_KEY =
  'bookflow.frontend.display-preferences.v1';

interface StoredDisplayPreferences {
  mascotScale: number;
  previewFontScale: number;
  previewZoom: number;
}

const finiteInRange = (
  value: unknown,
  minimum: number,
  maximum: number,
): value is number =>
  typeof value === 'number'
  && Number.isFinite(value)
  && value >= minimum
  && value <= maximum;

export function safeStoredDisplayPreferences(
  value: string | null,
): Partial<StoredDisplayPreferences> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value) as Partial<StoredDisplayPreferences>;
    return {
      ...(finiteInRange(parsed.mascotScale, 60, 160)
        && parsed.mascotScale % 5 === 0
        ? { mascotScale: parsed.mascotScale }
        : {}),
      ...(finiteInRange(parsed.previewFontScale, 80, 160)
        ? { previewFontScale: parsed.previewFontScale }
        : {}),
      // Legacy previewZoom remains parse-compatible but is reset during migration.
    };
  } catch {
    return {};
  }
}

export function createInitialPreferences(
  overrides: Partial<FrontendPreferences> = {},
): FrontendPreferences {
  const theme = overrides.theme ?? 'plum_editorial';
  const themeConfig = getThemeConfig(theme);
  return {
    uiLocale: 'zh-Hans',
    theme,
    activeCharacter: themeConfig.recommendedCharacter,
    activeSkin: 'skin_default',
    mascotForm: themeConfig.recommendedForm,
    mascotScale: 100,
    mascotVisible: true,
    mascotPosition: themeConfig.defaultMascotPosition,
    previewFontScale: 100,
    previewLayout: 'bilingual',
    ambientMotion: 'full',
    reducedMotion: false,
    helpOpen: false,
    ...overrides,
    previewZoom: 100,
  };
}

export function safeStoredMascotPosition(value: string | null): MascotPosition | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<MascotPosition>;
    if (
      typeof parsed.x === 'number'
      && Number.isFinite(parsed.x)
      && typeof parsed.y === 'number'
      && Number.isFinite(parsed.y)
      && (parsed.dock === 'left' || parsed.dock === 'right' || parsed.dock === 'free')
    ) {
      return { x: parsed.x, y: parsed.y, dock: parsed.dock };
    }
  } catch {
    return null;
  }
  return null;
}

export function themeFromValue(value: string): ThemeId {
  return value as ThemeId;
}
