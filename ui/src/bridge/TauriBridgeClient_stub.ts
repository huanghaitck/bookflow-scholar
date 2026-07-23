import type {
  BackendCapabilities,
  BackendEvent,
  BackendSnapshot,
  CommandEnvelope,
  CommandResponse,
} from '../contracts/generated/backend-v1_2';
import {
  BACKEND_CONTRACT_VERSION,
  COMMAND_SCHEMA_VERSION,
} from '../contracts/generated/backend-v1_2';
import type {
  BackendContractClient,
  BackendEventListener,
} from './BackendContractClient';

export const TAURI_BRIDGE_COMMAND = 'bookflow_bridge_command';
export const TAURI_BRIDGE_EVENT = 'bookflow://backend-event';

export interface TauriTransportPort {
  invoke<T>(command: string, args: Record<string, unknown>): Promise<T>;
  listen<T>(event: string, listener: (payload: T) => void): Promise<() => void>;
}

let commandSequence = 0;

function commandId(): string {
  commandSequence += 1;
  return `ui-tauri-stub-${Date.now()}-${commandSequence}`;
}

export class TauriBridgeClientStub implements BackendContractClient {
  private listeners = new Set<BackendEventListener>();
  private unlisten: (() => void) | null = null;

  constructor(private readonly transport: TauriTransportPort) {}

  async connect(): Promise<void> {
    if (this.unlisten) return;
    this.unlisten = await this.transport.listen<BackendEvent>(
      TAURI_BRIDGE_EVENT,
      (event) => this.listeners.forEach((listener) => listener(event)),
    );
  }

  async disconnect(): Promise<void> {
    this.unlisten?.();
    this.unlisten = null;
  }

  command<T = unknown>(envelope: CommandEnvelope): Promise<CommandResponse<T>> {
    return this.transport.invoke<CommandResponse<T>>(TAURI_BRIDGE_COMMAND, { envelope });
  }

  async getCapabilities(): Promise<BackendCapabilities> {
    const response = await this.command<BackendCapabilities>(this.envelope('getCapabilities'));
    if (!response.accepted) throw new Error('Tauri bridge rejected getCapabilities.');
    return response.result;
  }

  async getSnapshot(): Promise<BackendSnapshot> {
    const response = await this.command<BackendSnapshot>(this.envelope('getSnapshot'));
    if (!response.accepted) throw new Error('Tauri bridge rejected getSnapshot.');
    return response.result;
  }

  subscribe(listener: BackendEventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private envelope(command: 'getCapabilities' | 'getSnapshot'): CommandEnvelope {
    return {
      schema_version: COMMAND_SCHEMA_VERSION,
      contract_version: BACKEND_CONTRACT_VERSION,
      command_id: commandId(),
      command,
      payload: {},
    };
  }
}
