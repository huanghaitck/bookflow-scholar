import type { WorkflowState } from '../domain/bookflow-contract';

export type BackendLifecycle =
  | 'empty'
  | 'loading_project'
  | 'ready'
  | 'queued'
  | 'preprocessing'
  | 'ocr_running'
  | 'structure_rebuilding'
  | 'translation_running'
  | 'layout_rebuilding'
  | 'exporting'
  | 'paused'
  | 'recovering'
  | 'completed'
  | 'warning'
  | 'partial_success'
  | 'failed'
  | 'fatal_error'
  | 'cancelled'
  | 'long_idle'
  | WorkflowState;

const PRESENTATION_STATE: Readonly<Record<BackendLifecycle, WorkflowState>> = {
  empty: 'idle',
  loading_project: 'thinking',
  ready: 'idle',
  queued: 'thinking',
  preprocessing: 'working',
  ocr_running: 'working',
  structure_rebuilding: 'reviewing',
  translation_running: 'working',
  layout_rebuilding: 'reviewing',
  exporting: 'reviewing',
  paused: 'sleeping',
  recovering: 'thinking',
  completed: 'completed',
  warning: 'warning',
  partial_success: 'warning',
  failed: 'error',
  fatal_error: 'error',
  cancelled: 'idle',
  long_idle: 'sleeping',
  idle: 'idle',
  thinking: 'thinking',
  working: 'working',
  reviewing: 'reviewing',
  error: 'error',
  sleeping: 'sleeping',
};

export function derivePresentationState(lifecycle: string): WorkflowState {
  return PRESENTATION_STATE[lifecycle as BackendLifecycle] ?? 'warning';
}
