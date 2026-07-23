import { mapBackendSnapshot } from '../adapters/backendSnapshotAdapter';
import type { MockScenarioId } from '../contracts/generated/backend-v1_2';
import type { BookflowSnapshot } from '../domain/bookflow-contract';
import { ContractBookflowBridge } from './ContractBookflowBridge';
import { MockBridgeClient, createScenarioSnapshot } from './MockBridgeClient';

export const backendContractMock = new MockBridgeClient('translation_running');

export const createMockSnapshot = (): BookflowSnapshot =>
  mapBackendSnapshot(createScenarioSnapshot('translation_running'));

export class MockBookflowBridge extends ContractBookflowBridge {
  constructor(private readonly mockClient = backendContractMock) {
    super(mockClient, createMockSnapshot());
  }

  async setMockScenario(scenario: MockScenarioId): Promise<void> {
    this.mockClient.setScenario(scenario);
    await this.resyncFromBackend();
  }

  getBackendMock(): MockBridgeClient {
    return this.mockClient;
  }
}
