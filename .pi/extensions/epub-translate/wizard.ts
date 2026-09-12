export interface ModelDescriptor {
  provider: string;
  id: string;
  name?: string;
  reasoning?: boolean;
}

export interface ScopedModel {
  model: ModelDescriptor;
  thinkingLevel?: string;
}

export const THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;

export interface ScopeDocument {
  path: string;
  in_spine: boolean;
  is_nav: boolean;
}

export interface ScopeAnalysisInput {
  translate?: unknown;
  keep?: unknown;
  target_language_code?: unknown;
}

export interface NormalizedScopeAnalysis {
  translate: string[];
  keep: string[];
  targetLanguageCode?: string;
}

const LANGUAGE_CODES: Record<string, string> = {
  vietnamese: "vi",
  "tiếng việt": "vi",
  english: "en",
  "english (united states)": "en-US",
  "american english": "en-US",
  french: "fr",
  spanish: "es",
  german: "de",
  italian: "it",
  portuguese: "pt",
  japanese: "ja",
  chinese: "zh",
  korean: "ko",
  russian: "ru",
  arabic: "ar",
};

export function inferLanguageCode(language: string): string | undefined {
  const normalized = language.trim().toLocaleLowerCase();
  if (LANGUAGE_CODES[normalized]) return LANGUAGE_CODES[normalized];
  const base = normalized.split(/[,(]/, 1)[0]?.trim();
  return base ? LANGUAGE_CODES[base] : undefined;
}

function normalizeLanguageCode(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const parts = value.trim().split("-");
  if (!/^[a-z]{2,3}$/i.test(parts[0] || "")) return undefined;
  if (parts.slice(1).some((part) => !/^[A-Za-z0-9]{2,8}$/.test(part))) return undefined;
  return [parts[0].toLowerCase(), ...parts.slice(1).map((part) =>
    part.length === 2 || part.length === 3 ? part.toUpperCase() : part,
  )].join("-");
}

export function normalizeScopeAnalysis(
  input: ScopeAnalysisInput,
  documents: readonly ScopeDocument[],
): NormalizedScopeAnalysis {
  const known = new Set(documents.map((document) => document.path));
  const translate = new Set(
    Array.isArray(input.translate) ? input.translate.filter((path): path is string => typeof path === "string" && known.has(path)) : [],
  );
  const keep = new Set(
    Array.isArray(input.keep) ? input.keep.filter((path): path is string => typeof path === "string" && known.has(path)) : [],
  );
  for (const document of documents) {
    if (translate.has(document.path)) {
      keep.delete(document.path);
    } else if (!keep.has(document.path)) {
      (document.in_spine || document.is_nav ? translate : keep).add(document.path);
    }
  }
  return {
    translate: documents.filter((document) => translate.has(document.path)).map((document) => document.path),
    keep: documents.filter((document) => keep.has(document.path)).map((document) => document.path),
    targetLanguageCode: normalizeLanguageCode(input.target_language_code),
  };
}

export function selectableModels(
  scoped: readonly ScopedModel[],
  available: readonly ModelDescriptor[],
): ModelDescriptor[] {
  return scoped.length > 0 ? scoped.map((entry) => entry.model) : [...available];
}

export function modelIdentifier(model: Pick<ModelDescriptor, "provider" | "id">): string {
  return `${model.provider}/${model.id}`;
}

export function selectableThinkingLevels(model: Pick<ModelDescriptor, "reasoning">): string[] {
  return model.reasoning === false ? ["off"] : [...THINKING_LEVELS];
}

export function parseJsonResponse<T = unknown>(response: string): T {
  const trimmed = response.trim();
  const objectStart = trimmed.indexOf("{");
  const arrayStart = trimmed.indexOf("[");
  const starts = [objectStart, arrayStart].filter((index) => index >= 0);
  if (starts.length === 0) throw new Error("analysis process returned no JSON");
  const start = Math.min(...starts);
  const objectEnd = trimmed.lastIndexOf("}");
  const arrayEnd = trimmed.lastIndexOf("]");
  const end = Math.max(objectEnd, arrayEnd);
  if (end < start) throw new Error("analysis process returned incomplete JSON");
  try {
    return JSON.parse(trimmed.slice(start, end + 1)) as T;
  } catch (error) {
    throw new Error(`analysis process returned invalid JSON: ${String(error)}`);
  }
}
