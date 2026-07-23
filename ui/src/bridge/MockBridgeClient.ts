import type {
  BackendCapabilities,
  BackendError,
  BackendEvent,
  BackendSnapshot,
  CommandEnvelope,
  CommandResponse,
  ImportSourcesResult,
  MockScenarioId,
} from '../contracts/generated/backend-v1_2';
import {
  BACKEND_CONTRACT_VERSION,
  CAPABILITIES_SCHEMA_VERSION,
  ERROR_SCHEMA_VERSION,
  EVENT_SCHEMA_VERSION,
  SNAPSHOT_SCHEMA_VERSION,
} from '../contracts/generated/backend-v1_2';
import {
  ADAPTER_COMMANDS,
  DIRECT_COMMANDS,
  MOCK_SCENARIO_IDS,
  TRANSPORT_DEFERRED_COMMANDS,
} from '../contracts/backend-contract-catalog';
import type {
  BackendContractClient,
  BackendEventListener,
} from './BackendContractClient';

export {
  ADAPTER_COMMANDS,
  DIRECT_COMMANDS,
  MOCK_SCENARIO_IDS,
  TRANSPORT_DEFERRED_COMMANDS,
} from '../contracts/backend-contract-catalog';

export const MOCK_CAPABILITIES: BackendCapabilities = {
  schema_version: CAPABILITIES_SCHEMA_VERSION,
  contract_version: BACKEND_CONTRACT_VERSION,
  direct_commands: [...DIRECT_COMMANDS],
  adapter_commands: [...ADAPTER_COMMANDS],
  transport_deferred_commands: [...TRANSPORT_DEFERRED_COMMANDS],
  unsupported_capabilities: [
    'configureProvider_direct',
    'openOutputFolder_direct',
    'revealLogFile_direct',
    'immediate_stage_preemption',
    'parallel_workers',
    'typed_pipeline_progress_event',
    'typed_export_events',
    'typed_log_stream',
    'transport_connection_events',
  ],
  supportsWebAssist: true,
  supportsGlossaryReviewExport: true,
  supportsGlossaryReviewImport: true,
  supportsDifficultPageExport: true,
  supportsDifficultPageImport: true,
  supportsWebAssistDiffPreview: true,
  supportsIncrementalRebuild: true,
  supportsWebAssistUndo: true,
  supportsProviderConnectionTest: false,
  supportsProviderConfigurationEdit: false,
};

interface ScenarioPatch {
  connection_status?: string;
  active_project?: unknown;
  projects?: unknown[];
  active_batch?: unknown;
  batches?: unknown[];
  jobs?: unknown[];
  queue?: unknown;
  pipeline_phase?: string;
  current_stage?: string | null;
  aggregate_progress?: number;
  current_item?: string | null;
  total_items?: number;
  can_pause?: boolean;
  can_resume?: boolean;
  can_cancel?: boolean;
  can_retry?: boolean;
  pause_requested?: boolean;
  cancel_requested?: boolean;
  last_checkpoint?: string | null;
  warnings?: unknown[];
  errors?: unknown[];
  outputs?: unknown[];
}

const SCENARIO_PATCHES: Record<MockScenarioId, ScenarioPatch> = {
  backend_disconnected: { connection_status: 'disconnected', pipeline_phase: 'ready' },
  empty_project: { active_project: null, projects: [], pipeline_phase: 'empty', total_items: 0 },
  project_loading: { pipeline_phase: 'loading_project', current_stage: 'loading_project', aggregate_progress: 0.05 },
  project_ready: { pipeline_phase: 'ready', current_stage: null, aggregate_progress: 0 },
  importing_single_file: { pipeline_phase: 'importing', current_stage: 'importing', aggregate_progress: 0.12, current_item: 'chapter-01.pdf', total_items: 1, can_cancel: true },
  importing_folder: { pipeline_phase: 'importing', current_stage: 'importing', aggregate_progress: 0.18, current_item: 'volume-01.pdf', total_items: 12, can_cancel: true },
  partial_import_success: { pipeline_phase: 'ready', aggregate_progress: 0.25, total_items: 4, warnings: [{ code: 'partial_import', failed: 1 }] },
  batch_queued: { pipeline_phase: 'queued', current_stage: 'queued', aggregate_progress: 0, total_items: 6, can_pause: true, can_cancel: true },
  ocr_running: { pipeline_phase: 'running', current_stage: 'ocr_running', aggregate_progress: 0.28, current_item: 'chapter-03.pdf', total_items: 6, can_pause: true, can_cancel: true },
  structure_rebuilding: { pipeline_phase: 'running', current_stage: 'structure_rebuilding', aggregate_progress: 0.46, current_item: 'chapter-04.pdf', total_items: 6, can_pause: true, can_cancel: true },
  translation_running: { pipeline_phase: 'running', current_stage: 'translation_running', aggregate_progress: 0.63, current_item: 'chapter-05.pdf', total_items: 6, can_pause: true, can_cancel: true, last_checkpoint: 'checkpoint-translation-04' },
  exporting: { pipeline_phase: 'running', current_stage: 'exporting', aggregate_progress: 0.91, current_item: 'book.md', total_items: 6, can_cancel: true },
  paused: { pipeline_phase: 'paused', current_stage: 'translation_running', aggregate_progress: 0.63, total_items: 6, can_resume: true, can_cancel: true },
  recovering: { pipeline_phase: 'recovering', current_stage: 'recovering', aggregate_progress: 0.63, total_items: 6, can_cancel: true },
  warning: { pipeline_phase: 'warning', current_stage: 'translation_running', aggregate_progress: 0.76, total_items: 6, can_retry: true, warnings: [{ code: 'provider_retry', severity: 'warning' }] },
  failed: { pipeline_phase: 'failed', current_stage: 'translation_running', aggregate_progress: 0.76, total_items: 6, can_retry: true, errors: [{ code: 'provider_unavailable', severity: 'error' }] },
  completed: { pipeline_phase: 'completed', current_stage: null, aggregate_progress: 1, total_items: 6, outputs: [{ job_id: 'job-demo', path: 'D:\\Bookflow\\output\\book.md' }] },
  cancelled: { pipeline_phase: 'cancelled', current_stage: null, aggregate_progress: 0.42, total_items: 6, can_retry: true },
  schema_mismatch: { pipeline_phase: 'ready' },
  command_rejected: { pipeline_phase: 'ready' },
};

const now = () => new Date().toISOString();

function createBaseSnapshot(): BackendSnapshot {
  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    contract_version: BACKEND_CONTRACT_VERSION,
    snapshot_version: 1,
    generated_at: now(),
    backend_version: 'mock-1.1.0',
    connection_status: 'connected',
    capabilities: MOCK_CAPABILITIES,
    provider_status: [
      { provider_id: 'glm_vision', model_alias: 'vision-primary', status: 'ready' },
      { provider_id: 'deepseek_translation', model_alias: 'translation-primary', status: 'ready' },
    ],
    active_project: { project_id: 'project-demo', name: "The Cartographer's Garden", state: 'open' },
    projects: [{ project_id: 'project-demo', name: "The Cartographer's Garden", state: 'open' }],
    sources: [],
    active_batch: { batch_id: 'batch-demo', state: 'running' },
    batches: [{ batch_id: 'batch-demo', state: 'running' }],
    jobs: [],
    queue: { queued: 2, running: 1, completed: 3 },
    pipeline_phase: 'ready',
    current_stage: null,
    aggregate_progress: 0,
    current_item: null,
    total_items: 6,
    can_pause: false,
    can_resume: false,
    can_cancel: false,
    can_retry: false,
    pause_requested: false,
    cancel_requested: false,
    last_checkpoint: null,
    warnings: [],
    errors: [],
    outputs: [],
    usage_summary: { request_count: 0, retry_count: 0, total_tokens: 0 },
    last_event_sequence: 1,
    web_assist_packages: [],
    web_assist_history: [],
  };
}

export function createScenarioSnapshot(scenario: MockScenarioId): BackendSnapshot {
  const snapshot = { ...createBaseSnapshot(), ...SCENARIO_PATCHES[scenario] };
  if (scenario === 'schema_mismatch') {
    return { ...snapshot, schema_version: 'bookflow-snapshot-v9' } as unknown as BackendSnapshot;
  }
  return snapshot;
}

function errorEnvelope(code: string, message: string): BackendError {
  return {
    schema_version: ERROR_SCHEMA_VERSION,
    error_code: code,
    severity: 'error',
    user_message: message,
    technical_message: message,
    recoverable: true,
    retryable: false,
    stage: null,
    job_id: null,
    source_id: null,
    timestamp: now(),
    details: {},
  };
}

export class MockBridgeClient implements BackendContractClient {
  private listeners = new Set<BackendEventListener>();
  private responses = new Map<string, CommandResponse>();
  private scenario: MockScenarioId;
  private snapshot: BackendSnapshot;
  private sequence = 1;
  private snapshotRequests = 0;

  constructor(scenario: MockScenarioId = 'translation_running') {
    this.scenario = scenario;
    this.snapshot = createScenarioSnapshot(scenario);
  }

  async connect(): Promise<void> {
    if (this.scenario === 'backend_disconnected') this.setScenario('project_ready');
  }

  async disconnect(): Promise<void> {
    this.setScenario('backend_disconnected');
  }

  setScenario(scenario: MockScenarioId): void {
    this.scenario = scenario;
    this.snapshot = createScenarioSnapshot(scenario);
    this.sequence += 1;
    this.snapshot = {
      ...this.snapshot,
      snapshot_version: this.sequence,
      last_event_sequence: this.sequence,
    };
    this.emit('snapshot.updated', { snapshot_version: this.sequence });
  }

  getScenario(): MockScenarioId {
    return this.scenario;
  }

  async getCapabilities(): Promise<BackendCapabilities> {
    return MOCK_CAPABILITIES;
  }

  async getSnapshot(): Promise<BackendSnapshot> {
    this.snapshotRequests += 1;
    return { ...this.snapshot };
  }

  getSnapshotRequestCount(): number {
    return this.snapshotRequests;
  }

  emitTestEvent(
    eventType: BackendEvent['event_type'],
    options: {
      sequence?: number;
      eventId?: string;
      payload?: Record<string, unknown>;
    } = {},
  ): void {
    const sequence = options.sequence ?? this.sequence + 1;
    this.sequence = Math.max(this.sequence, sequence);
    this.snapshot = {
      ...this.snapshot,
      snapshot_version: Math.max(this.snapshot.snapshot_version, sequence),
      last_event_sequence: sequence,
      generated_at: now(),
    };
    this.emit(eventType, options.payload ?? {}, sequence, options.eventId);
  }

  async command<T = unknown>(envelope: CommandEnvelope): Promise<CommandResponse<T>> {
    const cached = this.responses.get(envelope.command_id);
    if (cached) return cached as CommandResponse<T>;
    const supported = (DIRECT_COMMANDS as readonly string[]).includes(envelope.command);
    const forcedRejection = this.scenario === 'command_rejected';
    if (!supported || forcedRejection) {
      const response = this.response<T>(envelope, false, null as T, errorEnvelope(
        supported ? 'command_rejected' : 'capability_unavailable',
        `${envelope.command} is unavailable in this bridge capability set.`,
      ));
      this.responses.set(envelope.command_id, response);
      return response;
    }

    let result: unknown = {};
    if (envelope.command === 'getCapabilities') result = MOCK_CAPABILITIES;
    if (envelope.command === 'getSnapshot') result = this.snapshot;
    if (envelope.command === 'importSources') {
      const paths = Array.isArray(envelope.payload.paths) ? envelope.payload.paths.filter((item): item is string => typeof item === 'string') : [];
      const failed = this.scenario === 'partial_import_success' ? Math.min(1, paths.length) : 0;
      result = {
        project_id: String(envelope.payload.project_id ?? 'project-demo'),
        batch_id: 'batch-import-demo',
        discovered: paths.length,
        imported: Math.max(0, paths.length - failed),
        linked: 0,
        duplicates: 0,
        failed,
        results: paths.map((sourcePath, index) => ({
          path: sourcePath,
          status: index < failed ? 'failed' : 'imported',
          error: index < failed ? errorEnvelope('unsupported_input', 'The mock rejected this item.') : null,
        })),
      } satisfies ImportSourcesResult;
    }
    if (envelope.command === 'pausePipeline') this.setScenario('paused');
    if (envelope.command === 'resumePipeline') this.setScenario('translation_running');
    if (envelope.command === 'cancelPipeline') this.setScenario('cancelled');
    if (envelope.command === 'retryFailedStage') this.setScenario('batch_queued');
    if (envelope.command === 'recoverFromCheckpoint') this.setScenario('recovering');
    const response = this.response(envelope, true, result as T, null);
    this.responses.set(envelope.command_id, response);
    return response;
  }

  subscribe(listener: BackendEventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(
    eventType: BackendEvent['event_type'],
    payload: Record<string, unknown>,
    sequence = this.sequence,
    eventId = `mock-event-${sequence}`,
  ): void {
    const event: BackendEvent = {
      schema_version: EVENT_SCHEMA_VERSION,
      contract_version: BACKEND_CONTRACT_VERSION,
      event_id: eventId,
      sequence,
      timestamp: now(),
      event_type: eventType,
      project_id: 'project-demo',
      batch_id: 'batch-demo',
      job_id: null,
      payload,
    };
    this.listeners.forEach((listener) => listener(event));
  }

  private response<T>(
    envelope: CommandEnvelope,
    accepted: boolean,
    result: T,
    error: BackendError | null,
  ): CommandResponse<T> {
    return {
      schema_version: 'bookflow-command-response-v1.2',
      contract_version: BACKEND_CONTRACT_VERSION,
      command_id: envelope.command_id,
      command: envelope.command,
      status: accepted ? 'accepted' : 'rejected',
      accepted,
      result,
      error,
      timestamp: now(),
    };
  }
}
