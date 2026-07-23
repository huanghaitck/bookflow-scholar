import {
  AlignLeft,
  BookOpenText,
  Cpu,
  FileOutput,
  FolderKanban,
  History,
  Globe2,
  Languages,
  LayoutDashboard,
  ListChecks,
  ListTree,
  ScanText,
  Settings,
} from 'lucide-react';
import type { MessageKey } from '../i18n/messages';

export type RouteId =
  | 'overview'
  | 'projects'
  | 'sourceDocument'
  | 'ocr'
  | 'structure'
  | 'translationWorkflow'
  | 'comparison'
  | 'outputFiles'
  | 'logs'
  | 'historyTasks'
  | 'modelServices'
  | 'webAssist'
  | 'settings';

export const NAVIGATION = [
  { id: 'overview', label: 'overview', icon: LayoutDashboard },
  { id: 'projects', label: 'projects', icon: FolderKanban },
  { id: 'sourceDocument', label: 'sourceDocument', icon: BookOpenText },
  { id: 'ocr', label: 'ocr', icon: ScanText },
  { id: 'structure', label: 'structure', icon: ListTree },
  { id: 'translationWorkflow', label: 'translationWorkflow', icon: Languages },
  { id: 'comparison', label: 'comparison', icon: AlignLeft },
  { id: 'outputFiles', label: 'outputFiles', icon: FileOutput },
  { id: 'logs', label: 'logs', icon: ListChecks },
  { id: 'historyTasks', label: 'historyTasks', icon: History },
  { id: 'modelServices', label: 'modelServices', icon: Cpu },
  { id: 'webAssist', label: 'webReview', icon: Globe2 },
  { id: 'settings', label: 'settings', icon: Settings },
] as const satisfies ReadonlyArray<{
  id: RouteId;
  label: MessageKey;
  icon: typeof LayoutDashboard;
}>;
