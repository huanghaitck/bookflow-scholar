import type { BookflowSnapshot } from '../domain/bookflow-contract';

export interface CommandAvailability {
  start: boolean;
  pause: boolean;
  resume: boolean;
  cancel: boolean;
  retry: boolean;
  recover: boolean;
  export: boolean;
  openOutputFolder: boolean;
  revealLogFile: boolean;
}

export function deriveCommandAvailability(snapshot: BookflowSnapshot): CommandAvailability {
  const direct = new Set(snapshot.backendCapabilities.directCommands);
  const transport = new Set(snapshot.backendCapabilities.transportDeferredCommands);
  const ready = snapshot.commandPending === null && snapshot.connectionState === 'connected';
  const activeContext = snapshot.backendState?.activeContext;
  const hasStartContext = Boolean(
    activeContext
    && typeof activeContext.active_project_id === 'string'
    && activeContext.active_project_id
    && typeof activeContext.active_source_id === 'string'
    && activeContext.active_source_id
    && typeof activeContext.active_batch_id === 'string'
    && activeContext.active_batch_id
    && typeof activeContext.active_job_id === 'string'
    && activeContext.active_job_id,
  );
  return {
    start: ready && hasStartContext && direct.has('startPipeline') && (
      ['idle', 'warning'].includes(snapshot.workflowState) || snapshot.currentStage === 'queued'
    ),
    pause: ready && direct.has('pausePipeline') && snapshot.canPause,
    resume: ready && direct.has('resumePipeline') && snapshot.canResume,
    cancel: ready && direct.has('cancelPipeline') && snapshot.canCancel,
    retry: ready && direct.has('retryFailedStage') && snapshot.canRetry,
    recover: ready && direct.has('recoverFromCheckpoint') && Boolean(snapshot.checkpoint),
    export: ready && direct.has('exportOutputs') && snapshot.availableOutputs.length > 0,
    openOutputFolder: ready && direct.has('openOutputFolder') && !transport.has('openOutputFolder'),
    revealLogFile: ready && direct.has('revealLogFile') && !transport.has('revealLogFile'),
  };
}
