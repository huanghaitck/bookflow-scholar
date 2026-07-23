import type {
  BookflowCommand,
  BookflowEvent,
  BookflowRequest,
  BookflowSnapshot,
  BridgeEnvelope,
  CommandResult,
} from "../domain/bookflow-contract";

export type BridgeEventListener = (event: BookflowEvent) => void;

export interface BookflowBridge {
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  command(
    command: BookflowCommand,
  ): Promise<BridgeEnvelope<CommandResult>>;
  request<T = unknown>(
    request: BookflowRequest,
  ): Promise<BridgeEnvelope<T>>;
  getCompleteSnapshot(): Promise<BridgeEnvelope<BookflowSnapshot>>;
  subscribe(listener: BridgeEventListener): () => void;
}
