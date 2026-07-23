export interface ResourceUriResolver {
  resolveReadOnly(nativePath: string): Promise<string | null>;
}

export const unavailableResourceUriResolver: ResourceUriResolver = {
  async resolveReadOnly(): Promise<null> {
    return null;
  },
};

export class TauriResourceUriResolverStub implements ResourceUriResolver {
  constructor(
    private readonly convertAllowlistedPath?: (nativePath: string) => Promise<string>,
  ) {}

  async resolveReadOnly(nativePath: string): Promise<string | null> {
    if (!this.convertAllowlistedPath || nativePath.trim() === '') return null;
    return this.convertAllowlistedPath(nativePath);
  }
}

export function resourceDisplayName(nativePath: string): string {
  return nativePath.split(/[\\/]/).filter(Boolean).pop() ?? 'resource';
}
