import type { BackendSnapshot as RawBackendSnapshot } from '../contracts/generated/backend-v1_2';
import type {
  AvailableOutput,
  BookflowSnapshot,
  ConnectionState,
  ProviderSummary,
  WorkflowState,
} from '../domain/bookflow-contract';
import { MOCK_LANGUAGE_CAPABILITIES } from '../domain/language-capabilities';
import { derivePresentationState } from './derivePresentationState';

const objectValue = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const stringValue = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback;

const booleanValue = (value: unknown): boolean => value === true;

const numberValue = (value: unknown, fallback = 0): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

const arrayValue = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

function connectionState(value: unknown): ConnectionState {
  return value === 'connected' || value === 'connecting' || value === 'recovering'
    ? value
    : 'disconnected';
}

function providerSummary(
  configurations: unknown[],
  providers: unknown[],
  kind: 'text' | 'vlm',
): ProviderSummary {
  const expectedRoles = kind === 'text' ? ['language', 'text'] : ['vision', 'vlm'];
  const configured = configurations.map(objectValue).filter(
    (item): item is Record<string, unknown> => item !== null,
  ).find((item) => expectedRoles.includes(stringValue(item.role).toLowerCase()));
  const candidates = providers.map(objectValue).filter((item): item is Record<string, unknown> => item !== null);
  const match = candidates.find((item) => {
    const role = stringValue(item.provider_role ?? item.role).toLowerCase();
    return expectedRoles.includes(role);
  }) ?? (kind === 'text' ? candidates[0] : undefined);
  const isConfigured = booleanValue(configured?.configured) && booleanValue(configured?.valid);
  return {
    provider: stringValue(configured?.display_name, kind === 'text' ? 'Language model' : 'Vision model'),
    modelAlias: stringValue(configured?.model, stringValue(match?.model_alias, 'Not advertised')),
    baseUrlLabel: stringValue(configured?.base_url, 'Backend-owned endpoint'),
    credentialAlias: stringValue(configured?.credential_source, 'Backend-owned credential'),
    status: isConfigured
      ? 'ready'
      : stringValue(match?.status) === 'unavailable' ? 'offline' : match ? 'ready' : 'unknown',
  };
}

function mappedOutputs(raw: unknown): AvailableOutput[] {
  return arrayValue(raw).map(objectValue).filter((item): item is Record<string, unknown> => item !== null).map((item, index) => {
    const path = stringValue(item.path ?? item.output_path);
    const extension = stringValue(item.format, path.split('.').pop()?.toLowerCase());
    const format = extension === 'docx' || extension === 'pdf' || extension === 'json' ? extension : 'md';
    return {
      id: stringValue(item.job_id, `output-${index + 1}`),
      format,
      displayName: stringValue(item.display_name, path ? path.split(/[\\/]/).pop() ?? 'Output file' : 'Output file'),
      status: 'ready',
      openable: Boolean(path),
    };
  });
}

function lifecycleState(raw: RawBackendSnapshot): WorkflowState {
  const warnings = arrayValue(raw.warnings);
  const errors = arrayValue(raw.errors);
  if (errors.length > 0) return 'error';
  if (warnings.length > 0) return 'warning';
  const phase = stringValue(raw.pipeline_phase, 'empty');
  const phaseOwnedStates = new Set([
    'paused',
    'recovering',
    'completed',
    'warning',
    'failed',
    'cancelled',
  ]);
  const lifecycle = phaseOwnedStates.has(phase)
    ? phase
    : stringValue(raw.current_stage) || phase;
  return derivePresentationState(lifecycle);
}

export function mapBackendSnapshot(raw: RawBackendSnapshot): BookflowSnapshot {
  const project = objectValue(raw.active_project);
  const totalUnits = Math.max(0, Math.round(numberValue(raw.total_items)));
  const completedUnits = Math.min(totalUnits, Math.round(numberValue(raw.aggregate_progress) * totalUnits));
  const capabilities = objectValue(raw.capabilities);
  const directCommands = arrayValue(capabilities?.direct_commands).filter((item): item is string => typeof item === 'string');
  const adapterCommands = arrayValue(capabilities?.adapter_commands).filter((item): item is string => typeof item === 'string');
  const transportCommands = arrayValue(capabilities?.transport_deferred_commands).filter((item): item is string => typeof item === 'string');
  const queue = objectValue(raw.queue);
  const presentationState = lifecycleState(raw);
  const activeContext = objectValue(raw.active_context) ?? {};
  const source = arrayValue(raw.sources).map(objectValue).filter(
    (item): item is Record<string, unknown> => item !== null,
  ).find((item) => item.source_id === activeContext.active_source_id);
  const supportedLanguages = ['zh-Hans', 'en', 'fr', 'de', 'ja', 'es'];
  const sourceLanguage = stringValue(
    activeContext.source_language,
    stringValue(source?.source_language),
  );
  const targetLanguage = stringValue(activeContext.target_language);
  return {
    schemaVersion: raw.schema_version,
    snapshotId: `backend-snapshot-${raw.snapshot_version}`,
    eventSequence: numberValue(raw.last_event_sequence),
    connectionState: connectionState(raw.connection_status),
    workspaceId: stringValue(project?.project_id) || null,
    workspaceName: stringValue(project?.name) || null,
    workflowState: presentationState,
    currentStage: stringValue(raw.current_stage, stringValue(raw.pipeline_phase, 'empty')),
    completedUnits,
    totalUnits,
    reviewQueueCount: numberValue(queue?.review, arrayValue(raw.warnings).length),
    providerStatus: {
      text: providerSummary(arrayValue(raw.provider_configuration), arrayValue(raw.provider_status), 'text'),
      vlm: providerSummary(arrayValue(raw.provider_configuration), arrayValue(raw.provider_status), 'vlm'),
    },
    rendererStatus: {
      docx: 'unavailable',
      pdf: directCommands.includes('exportOutputs') ? 'ready' : 'unavailable',
      office: 'unavailable',
    },
    availableOutputs: mappedOutputs(raw.outputs),
    languageCapabilities: MOCK_LANGUAGE_CAPABILITIES,
    sourceLanguageDetected: supportedLanguages.includes(sourceLanguage)
      ? sourceLanguage as BookflowSnapshot['sourceLanguageDetected'] : null,
    sourceLanguageSelected: supportedLanguages.includes(sourceLanguage)
      ? sourceLanguage as BookflowSnapshot['sourceLanguageSelected'] : 'auto-detect',
    targetLanguageSelected: supportedLanguages.includes(targetLanguage)
      ? targetLanguage as BookflowSnapshot['targetLanguageSelected'] : 'zh-Hans',
    mascotState: presentationState,
    errorCode: arrayValue(raw.errors).length > 0 ? 'BACKEND_REPORTED_ERROR' : null,
    warningCode: arrayValue(raw.warnings).length > 0 ? 'BACKEND_REPORTED_WARNING' : null,
    canPause: booleanValue(raw.can_pause),
    canResume: booleanValue(raw.can_resume),
    canCancel: booleanValue(raw.can_cancel),
    canRetry: booleanValue(raw.can_retry),
    pauseRequested: booleanValue(raw.pause_requested),
    cancelRequested: booleanValue(raw.cancel_requested),
    commandPending: null,
    checkpoint: stringValue(raw.last_checkpoint) || null,
    queueSummary: {
      queued: numberValue(queue?.queued),
      running: numberValue(queue?.running),
      completed: numberValue(queue?.completed),
      failed: numberValue(queue?.failed),
    },
    backendCapabilities: {
      directCommands,
      adapterCommands,
      transportDeferredCommands: transportCommands,
    },
    backendState: {
      activeContext,
      projects: arrayValue(raw.projects),
      sources: arrayValue(raw.sources),
      batches: arrayValue(raw.batches),
      jobs: arrayValue(raw.jobs),
      outputs: arrayValue(raw.outputs),
      webAssistPackages: arrayValue(raw.web_assist_packages),
      webAssistHistory: arrayValue(raw.web_assist_history),
      recentEvents: arrayValue(raw.recent_events),
      providerConfiguration: arrayValue(raw.provider_configuration),
    },
    updatedAt: stringValue(raw.generated_at, new Date().toISOString()),
  };
}
