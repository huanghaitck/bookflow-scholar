/* eslint-disable */
/**
 * Generated from BACKEND_CONTRACT_BUNDLE 1.1.0-r1.
 * Do not edit. Run: node scripts/generate-backend-contract-types.mjs
 * Sparse backend schemas intentionally become unknown rather than invented fields.
 */

export const BACKEND_CONTRACT_VERSION = "1.1.0" as const;
export const BACKEND_BUNDLE_REVISION = "1.1.0-r1" as const;
export const COMMAND_SCHEMA_VERSION = "1.1" as const;
export const SNAPSHOT_SCHEMA_VERSION = "bookflow-snapshot-v1.1" as const;
export const EVENT_SCHEMA_VERSION = "bookflow-event-v1.1" as const;
export const ERROR_SCHEMA_VERSION = "bookflow-error-v1.1" as const;
export const CAPABILITIES_SCHEMA_VERSION = "bookflow-capabilities-v1.1" as const;

export type JsonObject = Record<string, unknown>;

export type BackendCommandName =
  | "getCapabilities"
  | "getSnapshot"
  | "createProject"
  | "openProject"
  | "closeProject"
  | "importSources"
  | "selectSourceDocument"
  | "configureProvider"
  | "startPipeline"
  | "pausePipeline"
  | "resumePipeline"
  | "cancelPipeline"
  | "retryFailedStage"
  | "recoverFromCheckpoint"
  | "exportOutputs"
  | "openOutputFolder"
  | "revealLogFile"
  | "acknowledgeWarning";

export type KnownBackendEventType =
  | "snapshot.updated"
  | "project.loaded"
  | "project.closed"
  | "import.started"
  | "import.progress"
  | "import.completed"
  | "import.failed"
  | "pipeline.queued"
  | "pipeline.stage_started"
  | "pipeline.progress"
  | "pipeline.stage_completed"
  | "pipeline.paused"
  | "pipeline.resumed"
  | "pipeline.recovering"
  | "pipeline.completed"
  | "pipeline.warning"
  | "pipeline.failed"
  | "pipeline.cancelled"
  | "export.started"
  | "export.completed"
  | "export.failed"
  | "log.appended"
  | "capabilities.changed"
  | "backend.disconnected"
  | "backend.reconnected";
export type BackendEventType = KnownBackendEventType | (string & {});

export type MockScenarioId =
  | "backend_disconnected"
  | "empty_project"
  | "project_loading"
  | "project_ready"
  | "importing_single_file"
  | "importing_folder"
  | "partial_import_success"
  | "batch_queued"
  | "ocr_running"
  | "structure_rebuilding"
  | "translation_running"
  | "exporting"
  | "paused"
  | "recovering"
  | "warning"
  | "failed"
  | "completed"
  | "cancelled"
  | "schema_mismatch"
  | "command_rejected";

export interface CommandEnvelope<TPayload extends JsonObject = JsonObject> {
  schema_version: typeof COMMAND_SCHEMA_VERSION;
  contract_version: typeof BACKEND_CONTRACT_VERSION;
  command_id: string;
  command: BackendCommandName;
  payload: TPayload;
}

export interface CommandResponse<TResult = unknown> {
  schema_version: "bookflow-command-response-v1.1";
  contract_version: typeof BACKEND_CONTRACT_VERSION;
  command_id: unknown;
  command: unknown;
  status: "accepted" | "rejected";
  accepted: boolean;
  result: TResult;
  error: BackendError | null | unknown;
  timestamp: unknown;
  [futureField: string]: unknown;
}

export interface BackendError {
  schema_version: typeof ERROR_SCHEMA_VERSION;
  error_code: unknown;
  severity: "info" | "warning" | "error" | "fatal";
  user_message: unknown;
  technical_message: unknown;
  recoverable: boolean;
  retryable: boolean;
  stage: unknown;
  job_id: unknown;
  source_id: unknown;
  timestamp: unknown;
  details: JsonObject;
  [futureField: string]: unknown;
}

export interface BackendCapabilities {
  schema_version: typeof CAPABILITIES_SCHEMA_VERSION;
  contract_version: typeof BACKEND_CONTRACT_VERSION;
  direct_commands: unknown[];
  adapter_commands: unknown[];
  transport_deferred_commands: unknown[];
  unsupported_capabilities: unknown[];
  [futureField: string]: unknown;
}

export interface BackendSnapshot {
  schema_version: typeof SNAPSHOT_SCHEMA_VERSION;
  contract_version: typeof BACKEND_CONTRACT_VERSION;
  snapshot_version: number;
  generated_at: unknown;
  backend_version: unknown;
  connection_status: unknown;
  capabilities: unknown;
  provider_status: unknown;
  active_project: unknown;
  projects: unknown[];
  sources: unknown[];
  active_batch: unknown;
  batches: unknown[];
  jobs: unknown[];
  queue: unknown;
  pipeline_phase: unknown;
  current_stage: unknown;
  aggregate_progress: number;
  current_item: unknown;
  total_items: unknown;
  can_pause: unknown;
  can_resume: unknown;
  can_cancel: unknown;
  can_retry: unknown;
  pause_requested: unknown;
  cancel_requested: unknown;
  last_checkpoint: unknown;
  warnings: unknown;
  errors: unknown;
  outputs: unknown;
  usage_summary: unknown;
  last_event_sequence: unknown;
  sequence?: unknown;
  [futureField: string]: unknown;
}

export interface BackendEvent<TPayload extends JsonObject = JsonObject> {
  schema_version: typeof EVENT_SCHEMA_VERSION;
  contract_version: typeof BACKEND_CONTRACT_VERSION;
  event_id: unknown;
  sequence: number;
  timestamp: unknown;
  event_type: BackendEventType;
  project_id: unknown;
  batch_id: unknown;
  job_id: unknown;
  payload: TPayload;
  [futureField: string]: unknown;
}

export interface DocumentResourceResponse {
  job_id: string;
  output_path: string;
  resources: string[];
  [futureField: string]: unknown;
}

export interface ImportSourcesRequest extends JsonObject {
  project_id: string;
  paths: string[];
}

export type ImportSourceItemStatus = "imported" | "linked" | "duplicate" | "failed";
export interface ImportSourceItemResult extends JsonObject {
  status: ImportSourceItemStatus;
  path?: string;
  error?: BackendError | null;
}
export interface ImportSourcesResult extends JsonObject {
  project_id: string;
  batch_id: string | null;
  discovered: number;
  imported: number;
  linked: number;
  duplicates: number;
  failed: number;
  results: ImportSourceItemResult[];
}

export interface PipelineConfiguration extends JsonObject {
  source_language?: string;
  target_language?: string;
}

export interface ProviderStatus extends JsonObject {
  provider_id?: string;
  model_alias?: string;
  status?: string;
}

export interface UsageSummary extends JsonObject {
  request_count?: number;
  retry_count?: number;
  latency_seconds?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}
