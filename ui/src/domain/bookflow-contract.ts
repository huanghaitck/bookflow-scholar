export const BRIDGE_SCHEMA_VERSION = "1.2.0";

export type WorkflowState =
  | "idle"
  | "thinking"
  | "working"
  | "reviewing"
  | "completed"
  | "warning"
  | "error"
  | "sleeping";

export type UiLocale = "zh-Hans" | "en" | "fr" | "de" | "ja" | "es";
export type SourceLanguage = "auto-detect" | UiLocale;
export type TargetLanguage = UiLocale;
export type MascotForm = "full" | "chibi";
export type MascotDock = "free" | "left" | "right";
export type AmbientMotionMode = "full" | "reduced" | "off";

export interface MascotPosition {
  x: number;
  y: number;
  dock: MascotDock;
}
export type MascotCharacter =
  | "mascot_editor"
  | "mascot_scholar"
  | "mascot_explorer";
export type MascotSkin = "skin_default" | "skin_midnight_archivist";
export type ThemeId =
  | "plum_editorial"
  | "sakura_literary"
  | "atlas_expedition";
export type ConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "recovering";
export type ServiceHealth = "unknown" | "ready" | "degraded" | "offline";
export type RendererHealth = "unknown" | "ready" | "unavailable" | "error";

export interface ProviderSummary {
  provider: string;
  modelAlias: string;
  baseUrlLabel: string;
  credentialAlias: string;
  status: ServiceHealth;
  messageCode?: string;
}

export interface ProviderStatus {
  text: ProviderSummary;
  vlm: ProviderSummary;
}

export interface RendererStatus {
  docx: RendererHealth;
  pdf: RendererHealth;
  office: RendererHealth;
}

export interface AvailableOutput {
  id: string;
  format: "md" | "docx" | "pdf" | "json";
  displayName: string;
  status: "pending" | "building" | "ready" | "failed";
  openable: boolean;
}

export interface DirectedLanguagePair {
  source: UiLocale;
  target: UiLocale;
}

export interface LanguageCapabilities {
  sourceLanguages: readonly SourceLanguage[];
  targetLanguages: readonly TargetLanguage[];
  directedPairs: readonly DirectedLanguagePair[];
  sourceAutoDetect: boolean;
  capabilityVersion: string;
}

export interface BookflowSnapshot {
  schemaVersion: string;
  snapshotId: string;
  eventSequence: number;
  connectionState: ConnectionState;
  workspaceId: string | null;
  workspaceName: string | null;
  workflowState: WorkflowState;
  currentStage: string;
  completedUnits: number;
  totalUnits: number;
  reviewQueueCount: number;
  providerStatus: ProviderStatus;
  rendererStatus: RendererStatus;
  availableOutputs: readonly AvailableOutput[];
  languageCapabilities: LanguageCapabilities;
  sourceLanguageDetected: UiLocale | null;
  sourceLanguageSelected: SourceLanguage;
  targetLanguageSelected: TargetLanguage;
  mascotState: WorkflowState;
  errorCode: string | null;
  warningCode: string | null;
  canPause: boolean;
  canResume: boolean;
  canCancel: boolean;
  canRetry: boolean;
  pauseRequested: boolean;
  cancelRequested: boolean;
  commandPending: string | null;
  checkpoint: string | null;
  queueSummary: {
    queued: number;
    running: number;
    completed: number;
    failed: number;
  };
  backendCapabilities: {
    directCommands: readonly string[];
    adapterCommands: readonly string[];
    transportDeferredCommands: readonly string[];
  };
  backendState?: {
    activeContext: Readonly<Record<string, unknown>>;
    projects: readonly unknown[];
    sources: readonly unknown[];
    batches: readonly unknown[];
    jobs: readonly unknown[];
    outputs: readonly unknown[];
    webAssistPackages: readonly unknown[];
    webAssistHistory: readonly unknown[];
    recentEvents: readonly unknown[];
    providerConfiguration: readonly unknown[];
  };
  updatedAt: string;
}

export interface FrontendPreferences {
  uiLocale: UiLocale;
  theme: ThemeId;
  activeCharacter: MascotCharacter;
  activeSkin: MascotSkin;
  mascotForm: MascotForm;
  mascotScale: number;
  mascotVisible: boolean;
  mascotPosition: MascotPosition;
  previewZoom: number;
  previewFontScale: number;
  previewLayout: "source" | "target" | "bilingual";
  ambientMotion: AmbientMotionMode;
  reducedMotion: boolean;
  helpOpen: boolean;
}

export type BookflowCommand =
  | { type: "workflow.start" }
  | { type: "workflow.pause" }
  | { type: "workflow.resume" }
  | { type: "workflow.cancel" }
  | { type: "workflow.retry" }
  | { type: "workflow.recover" }
  | { type: "sources.import"; paths: string[] }
  | { type: "sources.select"; sourceId: string }
  | { type: "workspace.create"; name: string }
  | { type: "outputs.export" }
  | { type: "outputs.openFolder" }
  | { type: "logs.reveal" }
  | { type: "workspace.open"; workspaceId: string }
  | {
      type: "language.select";
      source: SourceLanguage;
      target: TargetLanguage;
    }
  | { type: "review.open"; reviewId: string }
  | { type: "output.open"; outputId: string }
  | { type: "output.reveal"; outputId: string }
  | { type: "output.copyPath"; outputId: string }
  | { type: "asset.resolve"; assetId: string }
  | { type: "artifact.read"; artifactId: string }
  | { type: "artifact.path"; artifactId: string }
  | { type: "artifact.page"; artifactId: string; page: number }
  | { type: "provider.test"; role: "language" | "vision" }
  | { type: "provider.save"; role: "language" | "vision"; baseUrl: string; model: string }
  | { type: "webAssist.create"; packageType: "glossary_review" | "difficult_pages" }
  | { type: "webAssist.get"; packageId: string }
  | { type: "webAssist.validate"; packageId: string; importPath: string }
  | { type: "webAssist.preview"; packageId: string }
  | { type: "webAssist.apply"; packageId: string }
  | { type: "webAssist.discard"; packageId: string }
  | { type: "webAssist.openFolder"; packageId: string }
  | { type: "webAssist.undo" };

export type BookflowRequest =
  | { type: "snapshot.get" }
  | { type: "language.capabilities.get" }
  | { type: "workspace.recent.list" };

export type BookflowEvent =
  | {
      type: "snapshot.replaced";
      sequence: number;
      snapshot: BookflowSnapshot;
    }
  | {
      type: "workflow.changed";
      sequence: number;
      workflowState: WorkflowState;
      currentStage: string;
      completedUnits: number;
      totalUnits: number;
      reviewQueueCount: number;
      mascotState: WorkflowState;
      canPause: boolean;
      canResume: boolean;
      canCancel: boolean;
    }
  | {
      type: "services.changed";
      sequence: number;
      providerStatus: ProviderStatus;
      rendererStatus: RendererStatus;
    }
  | {
      type: "outputs.changed";
      sequence: number;
      availableOutputs: readonly AvailableOutput[];
    }
  | {
      type: "language.changed";
      sequence: number;
      languageCapabilities: LanguageCapabilities;
      sourceLanguageDetected: UiLocale | null;
      sourceLanguageSelected: SourceLanguage;
      targetLanguageSelected: TargetLanguage;
    }
  | {
      type: "problem.changed";
      sequence: number;
      errorCode: string | null;
      warningCode: string | null;
      mascotState: WorkflowState;
    }
  | {
      type: "connection.changed";
      sequence: number;
      connectionState: ConnectionState;
    };

export interface BridgeEnvelope<T> {
  schemaVersion: string;
  requestId: string;
  data: T;
}

export interface CommandResult {
  accepted: boolean;
  commandId: string;
  reasonCode?: string;
  result?: unknown;
}
