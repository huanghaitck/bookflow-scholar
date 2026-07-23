import type {
  DirectedLanguagePair,
  LanguageCapabilities,
  SourceLanguage,
  TargetLanguage,
  UiLocale,
} from "./bookflow-contract";

export const UI_LOCALES = [
  "zh-Hans",
  "en",
  "fr",
  "de",
  "ja",
  "es",
] as const satisfies readonly UiLocale[];

export const SOURCE_LANGUAGES = [
  "auto-detect",
  ...UI_LOCALES,
] as const satisfies readonly SourceLanguage[];

export const TARGET_LANGUAGES = [
  ...UI_LOCALES,
] as const satisfies readonly TargetLanguage[];

export const DIRECTED_LANGUAGE_PAIRS: readonly DirectedLanguagePair[] =
  UI_LOCALES.flatMap((source) =>
    UI_LOCALES.filter((target) => target !== source).map((target) => ({
      source,
      target,
    })),
  );

if (DIRECTED_LANGUAGE_PAIRS.length !== 30) {
  throw new Error("The G1-R1 language fixture must contain 30 directed pairs.");
}

export const MOCK_LANGUAGE_CAPABILITIES: LanguageCapabilities = {
  sourceLanguages: SOURCE_LANGUAGES,
  targetLanguages: TARGET_LANGUAGES,
  directedPairs: DIRECTED_LANGUAGE_PAIRS,
  sourceAutoDetect: true,
  capabilityVersion: "g1-r1-mock.1",
};

export function supportsDirectedPair(
  source: SourceLanguage,
  target: TargetLanguage,
  capabilities: LanguageCapabilities,
): boolean {
  if (source === "auto-detect") {
    return capabilities.sourceAutoDetect;
  }
  return capabilities.directedPairs.some(
    (pair) => pair.source === source && pair.target === target,
  );
}
