import type {
  BackendCapabilities,
  BackendEvent,
  BackendSnapshot,
  CommandEnvelope,
  CommandResponse,
} from '../contracts/generated/backend-v1_2';

export type BackendEventListener = (event: BackendEvent) => void;

export interface BackendContractClient {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  command<T = unknown>(envelope: CommandEnvelope): Promise<CommandResponse<T>>;
  getCapabilities(): Promise<BackendCapabilities>;
  getSnapshot(): Promise<BackendSnapshot>;
  subscribe(listener: BackendEventListener): () => void;
}
