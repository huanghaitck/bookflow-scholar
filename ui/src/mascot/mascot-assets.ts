import type {
  BookflowSnapshot,
  MascotCharacter,
  MascotForm,
  MascotSkin,
} from '../domain/bookflow-contract';

export function mascotAssetUrl(
  character: MascotCharacter,
  skin: MascotSkin,
  form: MascotForm,
  state: BookflowSnapshot['mascotState'],
): string {
  const filename = `${character}__${skin}__${form}__${state}.png`;
  return `/production-candidates/${character}/${skin}/${form}/${filename}`;
}
