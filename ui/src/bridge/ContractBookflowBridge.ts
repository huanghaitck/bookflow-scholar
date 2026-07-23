import type {
  BackendCommandName,
  BackendEvent,
  BackendSnapshot,
  CommandEnvelope,
} from '../contracts/generated/backend-v1_2';
import {
  BACKEND_CONTRACT_VERSION,
  COMMAND_SCHEMA_VERSION,
} from '../contracts/generated/backend-v1_2';
import { SNAPSHOT_REFRESH_EVENT_TYPES } from '../contracts/backend-contract-catalog';
import type {
  BookflowCommand,
  BookflowEvent,
  BookflowRequest,
  BookflowSnapshot,
  BridgeEnvelope,
  CommandResult,
} from '../domain/bookflow-contract';
import { BRIDGE_SCHEMA_VERSION } from '../domain/bookflow-contract';
import { SnapshotEventStore } from '../state/SnapshotEventStore';
import type { BackendContractClient } from './BackendContractClient';
import type { BookflowBridge, BridgeEventListener } from './BookflowBridge';

const objectValue = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const stringValue = (value: unknown, fallback: string): string =>
  typeof value === 'string' && value.length > 0 ? value : fallback;

let requestSequence = 0;

function requestId(prefix: string): string {
  requestSequence += 1;
  return `${prefix}-${Date.now()}-${requestSequence}`;
}

export class ContractBookflowBridge implements BookflowBridge {
  protected readonly store: SnapshotEventStore;
  protected lastRawSnapshot: BackendSnapshot | null = null;
  private listeners = new Set<BridgeEventListener>();
  private unsubscribeBackend: (() => void) | null = null;
  private eventQueue: Promise<void> = Promise.resolve();

  constructor(
    protected readonly client: BackendContractClient,
    initialSnapshot: BookflowSnapshot,
  ) {
    this.store = new SnapshotEventStore(initialSnapshot);
    this.store.subscribe((snapshot) => this.emitSnapshot(snapshot));
  }

  async connect(): Promise<void> {
    this.store.setConnectionState('connecting');
    this.ensureBackendSubscription();
    try {
      await this.client.connect();
      await this.resyncFromBackend();
    } catch {
      this.store.setConnectionState('disconnected');
    }
  }

  async disconnect(): Promise<void> {
    await this.client.disconnect();
    this.unsubscribeBackend?.();
    this.unsubscribeBackend = null;
    this.store.setConnectionState('disconnected');
  }

  async reconnect(): Promise<void> {
    this.store.setConnectionState('recovering');
    this.ensureBackendSubscription();
    await this.client.disconnect();
    await this.client.connect();
    await this.resyncFromBackend();
  }

  async command(command: BookflowCommand): Promise<BridgeEnvelope<CommandResult>> {
    if (command.type === 'language.select') {
      this.store.setLanguages(command.source, command.target);
      return this.envelope({ accepted: true, commandId: requestId('frontend-language') });
    }

    const backendCommand = this.backendCommand(command);
    if (!backendCommand) {
      return this.envelope({
        accepted: false,
        commandId: requestId('unmapped-command'),
        reasonCode: 'not_mapped',
      });
    }

    const commandId = requestId('ui-command');
    this.store.setCommandPending(backendCommand);
    try {
      const response = await this.client.command(
        this.backendEnvelope(backendCommand, commandId, this.commandPayload(command)),
      );
      await this.resyncFromBackend();
      return this.envelope({
        accepted: response.accepted,
        commandId,
        result: response.result,
        reasonCode: response.accepted ? undefined : stringValue(
          objectValue(response.error)?.error_code,
          'backend_rejected',
        ),
      });
    } catch {
      this.store.setCommandPending(null);
      this.store.setConnectionState('recovering');
      return this.envelope({ accepted: false, commandId, reasonCode: 'transport_error' });
    }
  }

  async request<T = unknown>(request: BookflowRequest): Promise<BridgeEnvelope<T>> {
    if (request.type === 'snapshot.get') {
      return this.envelope(this.store.getSnapshot() as T);
    }
    if (request.type === 'language.capabilities.get') {
      return this.envelope(this.store.getSnapshot().languageCapabilities as T);
    }
    const projects = Array.isArray(this.lastRawSnapshot?.projects)
      ? this.lastRawSnapshot.projects.map(objectValue).filter(
          (item): item is Record<string, unknown> => item !== null,
        ).map((project) => ({
          id: stringValue(project.project_id, 'unknown-project'),
          name: stringValue(project.name, 'Unnamed project'),
        }))
      : [];
    return this.envelope(projects as T);
  }

  async getCompleteSnapshot(): Promise<BridgeEnvelope<BookflowSnapshot>> {
    return this.envelope(this.store.getSnapshot());
  }

  subscribe(listener: BridgeEventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  replaceSnapshot(snapshot: BookflowSnapshot): void {
    this.store.replacePresentationSnapshot(snapshot);
  }

  async resyncFromBackend(): Promise<void> {
    const raw = await this.client.getSnapshot();
    this.lastRawSnapshot = raw;
    this.store.replaceBackendSnapshot(raw);
  }

  async flushEvents(): Promise<void> {
    await this.eventQueue;
  }

  protected backendEnvelope(
    command: BackendCommandName,
    commandId: string,
    payload: Record<string, unknown>,
  ): CommandEnvelope {
    return {
      schema_version: COMMAND_SCHEMA_VERSION,
      contract_version: BACKEND_CONTRACT_VERSION,
      command_id: commandId,
      command,
      payload,
    };
  }

  private async handleBackendEvent(event: BackendEvent): Promise<void> {
    const decision = this.store.acceptEvent(event);
    if (decision === 'duplicate' || decision === 'out_of_order') return;
    if (decision === 'schema_mismatch') {
      this.store.setConnectionState('recovering');
      return;
    }
    if (
      decision === 'sequence_gap'
      || SNAPSHOT_REFRESH_EVENT_TYPES.has(event.event_type)
    ) {
      await this.resyncFromBackend();
    }
  }

  private ensureBackendSubscription(): void {
    if (this.unsubscribeBackend) return;
    this.unsubscribeBackend = this.client.subscribe((event) => {
      this.eventQueue = this.eventQueue
        .then(() => this.handleBackendEvent(event))
        .catch(() => this.store.setConnectionState('recovering'));
    });
  }

  private backendCommand(command: BookflowCommand): BackendCommandName | null {
    const mapping: Partial<Record<BookflowCommand['type'], BackendCommandName>> = {
      'workflow.start': 'startPipeline',
      'workflow.pause': 'pausePipeline',
      'workflow.resume': 'resumePipeline',
      'workflow.cancel': 'cancelPipeline',
      'workflow.retry': 'retryFailedStage',
      'workflow.recover': 'recoverFromCheckpoint',
      'sources.import': 'importSources',
      'sources.select': 'selectSourceDocument',
      'workspace.create': 'createProject',
      'outputs.export': 'exportOutputs',
      'outputs.openFolder': 'openOutputFolder',
      'output.open': 'openArtifact',
      'output.reveal': 'revealArtifact',
      'output.copyPath': 'getArtifactPath',
      'asset.resolve': 'resolveAsset',
      'artifact.read': 'readArtifact',
      'artifact.path': 'getArtifactPath',
      'artifact.page': 'renderArtifactPage',
      'logs.reveal': 'revealLogFile',
      'workspace.open': 'openProject',
      'provider.test': 'testProviderConnection',
      'provider.save': 'configureProvider',
      'webAssist.create': 'createWebAssistPackage',
      'webAssist.get': 'getWebAssistPackage',
      'webAssist.validate': 'validateWebAssistImport',
      'webAssist.preview': 'previewWebAssistDiff',
      'webAssist.apply': 'applyWebAssistImport',
      'webAssist.discard': 'discardWebAssistPackage',
      'webAssist.openFolder': 'openWebAssistPackageFolder',
      'webAssist.undo': 'undoWebAssistApply',
    };
    return mapping[command.type] ?? null;
  }

  private commandPayload(command: BookflowCommand): Record<string, unknown> {
    const activeProject = objectValue(this.lastRawSnapshot?.active_project);
    const activeContext = objectValue(this.lastRawSnapshot?.active_context);
    const activeBatch = objectValue(this.lastRawSnapshot?.active_batch);
    const projectId = stringValue(activeContext?.active_project_id, stringValue(activeProject?.project_id, ''));
    const sourceId = stringValue(activeContext?.active_source_id, '');
    const batchId = stringValue(activeContext?.active_batch_id, stringValue(activeBatch?.batch_id, ''));
    const jobId = stringValue(
      activeContext?.active_job_id,
      '',
    );

    switch (command.type) {
      case 'sources.import':
        return {
          project_id: projectId,
          paths: command.paths,
          pipeline_config: {
            source_language: this.store.getSnapshot().sourceLanguageSelected === 'auto-detect'
              ? 'auto' : this.store.getSnapshot().sourceLanguageSelected,
            target_language: this.store.getSnapshot().targetLanguageSelected,
            translation_enabled: true,
            structure_enabled: true,
            output_formats: ['md', 'docx', 'pdf'],
          },
        };
      case 'sources.select':
        return { project_id: projectId, source_id: command.sourceId };
      case 'workspace.open':
        return { project_id: command.workspaceId };
      case 'workspace.create':
        return { name: command.name };
      case 'workflow.start':
      case 'workflow.pause':
      case 'workflow.resume':
      case 'workflow.recover':
        return { batch_id: batchId };
      case 'workflow.cancel':
      case 'workflow.retry':
      case 'outputs.export':
        return { job_id: jobId };
      case 'asset.resolve':
        return { asset_id: command.assetId };
      case 'artifact.read':
      case 'artifact.path':
        return { artifact_id: command.artifactId };
      case 'artifact.page':
        return { artifact_id: command.artifactId, page_number: command.page };
      case 'output.open':
      case 'output.reveal':
      case 'output.copyPath':
        return { artifact_id: command.outputId };
      case 'provider.test':
        return { role: command.role };
      case 'provider.save':
        return { role: command.role, base_url: command.baseUrl, model: command.model };
      case 'webAssist.create':
        return {
          project_id: projectId,
          source_document_id: sourceId,
          package_type: command.packageType,
        };
      case 'webAssist.get':
        return { package_id: command.packageId, source_document_id: sourceId };
      case 'webAssist.validate':
        return {
          package_id: command.packageId,
          import_path: command.importPath,
          source_document_id: sourceId,
        };
      case 'webAssist.preview':
      case 'webAssist.apply':
      case 'webAssist.discard':
        return { package_id: command.packageId, source_document_id: sourceId };
      case 'webAssist.openFolder':
        return { package_id: command.packageId, source_document_id: sourceId };
      case 'webAssist.undo':
        return { project_id: projectId, source_document_id: sourceId };
      default:
        return {};
    }
  }

  private emitSnapshot(snapshot: BookflowSnapshot): void {
    const event: BookflowEvent = {
      type: 'snapshot.replaced',
      sequence: snapshot.eventSequence,
      snapshot,
    };
    this.listeners.forEach((listener) => listener(event));
  }

  private envelope<T>(data: T): BridgeEnvelope<T> {
    return {
      schemaVersion: BRIDGE_SCHEMA_VERSION,
      requestId: requestId('bridge-request'),
      data,
    };
  }
}
