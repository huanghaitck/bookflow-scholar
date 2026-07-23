import type {
  BookflowCommand,
  BookflowEvent,
  BookflowRequest,
  BookflowSnapshot,
  BridgeEnvelope,
  CommandResult,
} from '../domain/bookflow-contract';
import type { BookflowBridge, BridgeEventListener } from './BookflowBridge';

export class BookflowBridgeRouter implements BookflowBridge {
  private listeners = new Map<BridgeEventListener, () => void>();

  constructor(private activeBridge: BookflowBridge) {}

  setBridge(nextBridge: BookflowBridge): void {
    this.listeners.forEach((unsubscribe) => unsubscribe());
    this.activeBridge = nextBridge;
    this.listeners.forEach((_, listener) => {
      this.listeners.set(listener, this.activeBridge.subscribe(listener));
    });
  }

  getBridge(): BookflowBridge {
    return this.activeBridge;
  }

  connect(): Promise<void> {
    return this.activeBridge.connect();
  }

  disconnect(): Promise<void> {
    return this.activeBridge.disconnect();
  }

  command(command: BookflowCommand): Promise<BridgeEnvelope<CommandResult>> {
    return this.activeBridge.command(command);
  }

  request<T = unknown>(request: BookflowRequest): Promise<BridgeEnvelope<T>> {
    return this.activeBridge.request<T>(request);
  }

  getCompleteSnapshot(): Promise<BridgeEnvelope<BookflowSnapshot>> {
    return this.activeBridge.getCompleteSnapshot();
  }

  subscribe(listener: BridgeEventListener): () => void {
    this.listeners.set(listener, this.activeBridge.subscribe(listener));
    return () => {
      this.listeners.get(listener)?.();
      this.listeners.delete(listener);
    };
  }

  emitForTest(event: BookflowEvent): void {
    this.listeners.forEach((_, listener) => listener(event));
  }
}
