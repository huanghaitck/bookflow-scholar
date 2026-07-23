import type {
  MascotCharacter,
  MascotForm,
  MascotPosition,
  ThemeId,
} from '../domain/bookflow-contract';

export type ThemeLayoutId =
  | 'plum-reader-rail'
  | 'sakura-study-columns'
  | 'atlas-open-book';

export interface ThemeConfig {
  id: ThemeId;
  label: string;
  displayName: string;
  layoutId: ThemeLayoutId;
  referenceFile: string;
  recommendedCharacter: MascotCharacter;
  recommendedForm: MascotForm;
  defaultMascotPosition: MascotPosition;
}

export const THEME_CONFIGS: readonly ThemeConfig[] = [
  {
    id: 'plum_editorial',
    label: 'Plum Editorial',
    displayName: '紫藤编辑室',
    layoutId: 'plum-reader-rail',
    referenceFile: '01_ELEANOR_PLUM_EDITORIAL_REFERENCE.png',
    recommendedCharacter: 'mascot_editor',
    recommendedForm: 'chibi',
    defaultMascotPosition: { x: 232, y: 520, dock: 'free' },
  },
  {
    id: 'sakura_literary',
    label: 'Sakura Literary',
    displayName: '樱笺书房',
    layoutId: 'sakura-study-columns',
    referenceFile: '02_CLARA_SAKURA_LITERARY_REFERENCE.png',
    recommendedCharacter: 'mascot_scholar',
    recommendedForm: 'chibi',
    defaultMascotPosition: { x: 232, y: 518, dock: 'free' },
  },
  {
    id: 'atlas_expedition',
    label: 'Atlas Expedition',
    displayName: '航图探险室',
    layoutId: 'atlas-open-book',
    referenceFile: '03_STELLA_ATLAS_EXPEDITION_REFERENCE.png',
    recommendedCharacter: 'mascot_explorer',
    recommendedForm: 'chibi',
    defaultMascotPosition: { x: 232, y: 516, dock: 'free' },
  },
] as const;

export function getThemeConfig(theme: ThemeId): ThemeConfig {
  return THEME_CONFIGS.find((candidate) => candidate.id === theme)
    ?? THEME_CONFIGS[0];
}
