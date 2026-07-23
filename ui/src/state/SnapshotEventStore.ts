import { mapBackendSnapshot } from '../adapters/backendSnapshotAdapter';
import type {
  BackendEvent,
  BackendSnapshot,
} from '../contracts/generated/backend-v1_2';
import {
  BACKEND_CONTRACT_VERSION,
  EVENT_SCHEMA_VERSION,
  SNAPSHOT_SCHEMA_VERSION,
} from '../contracts/generated/backend-v1_2';
import type {
  BookflowSnapshot,
  ConnectionState,
  SourceLanguage,
  TargetLanguage,
} from '../domain/bookflow-contract';

export type EventDecision =
  | 'accepted'
  | 'duplicate'
  | 'out_of_order'
  | 'sequence_gap'
  | 'schema_mismatch';

type SnapshotListener = (snapshot: BookflowSnapshot) => void;

export class SnapshotEventStore {
  private snapshot: BookflowSnapshot;
  private listeners = new Set<SnapshotListener>();
  private seenEventIds = new Set<string>();
  private lastEventSequence: number;

  constructor(initialSnapshot: BookflowSnapshot) {
    this.snapshot = { ...initialSnapshot };
    this.lastEventSequence = initialSnapshot.eventSequence;
  }

  getSnapshot(): BookflowSnapshot {
    return { ...this.snapshot };
  }

  subscribe(listener: SnapshotListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  replaceBackendSnapshot(raw: BackendSnapshot): void {
    const selected = {
      sourceLanguageSelected: this.snapshot.sourceLanguageSelected,
      targetLanguageSelected: this.snapshot.targetLanguageSelected,
    };
    const mapped = mapBackendSnapshot(raw);
    const activeContext = raw.active_context && typeof raw.active_context === 'object'
      ? raw.active_context as Record<string, unknown>
      : null;
    const hasActiveSource = typeof activeContext?.active_source_id === 'string'
      && activeContext.active_source_id.length > 0;
    const preserveLocalSelection = mapped.workspaceId !== null
      && mapped.workspaceId === this.snapshot.workspaceId
      && !hasActiveSource;
    const compatible = raw.contract_version === BACKEND_CONTRACT_VERSION
      && raw.schema_version === SNAPSHOT_SCHEMA_VERSION;
    this.snapshot = compatible
      ? {
          ...mapped,
          ...(preserveLocalSelection ? selected : {}),
          commandPending: null,
        }
      : {
          ...mapped,
          ...(preserveLocalSelection ? selected : {}),
          connectionState: 'recovering',
          workflowState: 'warning',
          mascotState: 'warning',
          warningCode: 'BACKEND_SCHEMA_MISMATCH',
          commandPending: null,
          backendCapabilities: {
            directCommands: [],
            adapterCommands: [],
            transportDeferredCommands: [],
          },
        };
    this.lastEventSequence = Number.isFinite(Number(raw.last_event_sequence))
      ? Number(raw.last_event_sequence)
      : mapped.eventSequence;
    this.seenEventIds.clear();
    this.notify();
  }

  replacePresentationSnapshot(snapshot: BookflowSnapshot): void {
    this.snapshot = { ...snapshot };
    this.lastEventSequence = snapshot.eventSequence;
    this.notify();
  }

  setConnectionState(connectionState: ConnectionState): void {
    this.snapshot = {
      ...this.snapshot,
      connectionState,
      updatedAt: new Date().toISOString(),
    };
    this.notify();
  }

  setCommandPending(commandPending: string | null): void {
    this.snapshot = { ...this.snapshot, commandPending };
    this.notify();
  }

  setLanguages(source: SourceLanguage, target: TargetLanguage): void {
    this.snapshot = {
      ...this.snapshot,
      sourceLanguageSelected: source,
      targetLanguageSelected: target,
      updatedAt: new Date().toISOString(),
    };
    this.notify();
  }

  acceptEvent(event: BackendEvent): EventDecision {
    if (
      event.contract_version !== BACKEND_CONTRACT_VERSION
      || event.schema_version !== EVENT_SCHEMA_VERSION
    ) {
      return 'schema_mismatch';
    }
    const eventId = String(event.event_id);
    if (this.seenEventIds.has(eventId)) return 'duplicate';
    if (event.sequence <= this.lastEventSequence) return 'out_of_order';
    if (this.lastEventSequence > 0 && event.sequence > this.lastEventSequence + 1) {
      return 'sequence_gap';
    }
    this.seenEventIds.add(eventId);
    this.lastEventSequence = event.sequence;
    return 'accepted';
  }

  getEventSequence(): number {
    return this.lastEventSequence;
  }

  private notify(): void {
    const snapshot = this.getSnapshot();
    this.listeners.forEach((listener) => listener(snapshot));
  }
}
