import type {
  AmbientMotionMode,
  MascotCharacter,
  MascotForm,
  MascotSkin,
  WorkflowState,
} from '../../domain/bookflow-contract';
import { mascotAssetUrl } from '../mascot-assets';

export type MascotMotionMode = AmbientMotionMode;

export interface MascotRuntimeSelection {
  character: MascotCharacter;
  skin: MascotSkin;
  form: MascotForm;
  state: WorkflowState;
  ambientMotion: AmbientMotionMode;
  reducedMotion: boolean;
}

export interface MascotRuntimePresentation {
  requestedUrl: string;
  idleFallbackUrl: string;
  effectUrl: string;
  effectId: `state_${WorkflowState}`;
  motion: MascotMotionMode;
}

export interface MascotRuntimeAdapter {
  resolve(selection: MascotRuntimeSelection): MascotRuntimePresentation;
}

export class LocalCodeDrivenMascotAdapter implements MascotRuntimeAdapter {
  resolve(selection: MascotRuntimeSelection): MascotRuntimePresentation {
    return {
      requestedUrl: mascotAssetUrl(
        selection.character,
        selection.skin,
        selection.form,
        selection.state,
      ),
      idleFallbackUrl: mascotAssetUrl(
        selection.character,
        selection.skin,
        selection.form,
        'idle',
      ),
      effectUrl: '/effects/status-orbit.json',
      effectId: `state_${selection.state}`,
      motion: selection.reducedMotion ? 'reduced' : selection.ambientMotion,
    };
  }
}

export const mascotRuntimeAdapter = new LocalCodeDrivenMascotAdapter();
