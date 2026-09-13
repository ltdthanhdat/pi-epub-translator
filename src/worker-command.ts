export const WORKER_TOOL_NAMES = [
  "epub_claim_job",
  "epub_complete_job",
  "epub_spawn_worker",
  "epub_merge_run",
] as const;

export const WORKER_PROMPT = [
  "You are an isolated EPUB translation worker.",
  "Call epub_claim_job exactly once. If it returns no job, stop.",
  "Translate only the claimed XHTML fragment into the configured target language.",
  "Preserve every tag, attribute, namespace, link, and KEEP_PAGEBREAK marker exactly.",
  "Return no explanation and no Markdown fences in the translated string.",
  "Call epub_complete_job with the claimed job id, lease token, and translated XHTML.",
  "Then call epub_spawn_worker. If it reports merge ownership, call epub_merge_run.",
  "Do not use files, shell commands, or any tool other than the EPUB worker tools.",
].join(" ");

export interface WorkerCommand {
  command: string;
  args: string[];
}

export function buildWorkerCommand(
  _runDir: string,
  model: string,
  thinking: string,
): WorkerCommand {
  return {
    command: process.env.PI_BIN || "pi",
    args: [
      "--approve",
      "--no-session",
      "--no-context-files",
      "--no-skills",
      "--no-prompt-templates",
      "--no-builtin-tools",
      "--tools",
      WORKER_TOOL_NAMES.join(","),
      "--model",
      model,
      "--thinking",
      thinking,
      "-p",
      WORKER_PROMPT,
    ],
  };
}
