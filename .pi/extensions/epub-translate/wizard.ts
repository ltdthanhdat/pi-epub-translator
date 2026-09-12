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
