import type { BookflowSnapshot } from '../domain/bookflow-contract';
import { MOCK_LANGUAGE_CAPABILITIES } from '../domain/language-capabilities';

export function createDisconnectedSnapshot(): BookflowSnapshot {
  return {
    schemaVersion: '1.2.0',
    snapshotId: 'frontend-disconnected-initial',
    eventSequence: 0,
    connectionState: 'disconnected',
    workspaceId: null,
    workspaceName: null,
    workflowState: 'sleeping',
    currentStage: 'empty',
    completedUnits: 0,
    totalUnits: 0,
    reviewQueueCount: 0,
    providerStatus: {
      text: {
        provider: 'Text Provider',
        modelAlias: 'Not advertised',
        baseUrlLabel: 'Backend-owned endpoint',
        credentialAlias: 'Backend-owned credential',
        status: 'unknown',
      },
      vlm: {
        provider: 'VLM Provider',
        modelAlias: 'Not advertised',
        baseUrlLabel: 'Backend-owned endpoint',
        credentialAlias: 'Backend-owned credential',
        status: 'unknown',
      },
    },
    rendererStatus: { docx: 'unknown', pdf: 'unknown', office: 'unknown' },
    availableOutputs: [],
    languageCapabilities: MOCK_LANGUAGE_CAPABILITIES,
    sourceLanguageDetected: null,
    sourceLanguageSelected: 'auto-detect',
    targetLanguageSelected: 'zh-Hans',
    mascotState: 'sleeping',
    errorCode: null,
    warningCode: null,
    canPause: false,
    canResume: false,
    canCancel: false,
    canRetry: false,
    pauseRequested: false,
    cancelRequested: false,
    commandPending: null,
    checkpoint: null,
    queueSummary: { queued: 0, running: 0, completed: 0, failed: 0 },
    backendCapabilities: {
      directCommands: [],
      adapterCommands: [],
      transportDeferredCommands: [],
    },
    backendState: {
      activeContext: {},
      projects: [], sources: [], batches: [], jobs: [], outputs: [], webAssistPackages: [],
      webAssistHistory: [], recentEvents: [], providerConfiguration: [],
    },
    updatedAt: new Date(0).toISOString(),
  };
}
