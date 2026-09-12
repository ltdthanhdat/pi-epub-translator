import { readdir, stat } from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { formatProgress, type RunSummary } from "./progress.ts";
import {
  modelIdentifier,
  parseJsonResponse,
  selectableModels,
  selectableThinkingLevels,
  type ModelDescriptor,
  type ScopedModel,
} from "./wizard.ts";
import { registerWorkerTools, runHelper, spawnDetachedWorker } from "./worker-tools.ts";

interface DocumentInfo {
  path: string;
  in_spine: boolean;
  is_nav: boolean;
  properties: string[];
  epub_types: string[];
  title: string;
  sample: string;
}

interface GlossaryEntry {
  source: string;
  target: string;
  note?: string;
}

interface GlossaryAnalysis {
  entries?: GlossaryEntry[];
}

let progressTimer: ReturnType<typeof setInterval> | undefined;
let currentRunDir: string | undefined;

function modelLabel(model: ModelDescriptor): string {
  return model.name ? `${modelIdentifier(model)} — ${model.name}` : modelIdentifier(model);
}

function asModelDescriptor(value: unknown): ModelDescriptor {
  const model = value as Partial<ModelDescriptor>;
  if (!model.provider || !model.id) throw new Error("Pi model catalog returned an invalid model");
  return {
    provider: model.provider,
    id: model.id,
    name: model.name,
    reasoning: model.reasoning,
  };
}

async function inputValue(ctx: ExtensionContext, title: string, placeholder: string): Promise<string | undefined> {
  const value = await ctx.ui.input(title, placeholder);
  return value?.trim() || undefined;
}

async function listBooks(cwd: string): Promise<string[]> {
  const inputDir = join(cwd, "input");
  const entries = await readdir(inputDir, { withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && extname(entry.name).toLowerCase() === ".epub")
    .map((entry) => entry.name)
    .sort((left, right) => left.localeCompare(right));
}

async function chooseBook(ctx: ExtensionContext): Promise<string | undefined> {
  const books = await listBooks(ctx.cwd);
  if (books.length === 0) throw new Error(`No EPUB files found in ${join(ctx.cwd, "input")}`);
  return ctx.ui.select("Source EPUB", books);
}

function catalogModels(ctx: ExtensionContext): ModelDescriptor[] {
  const scoped = (ctx.scopedModels || []) as unknown as ScopedModel[];
  const available = (ctx.modelRegistry.getAvailable?.() || []) as unknown[];
  return selectableModels(scoped, available.map(asModelDescriptor));
}

async function chooseModel(ctx: ExtensionContext): Promise<ModelDescriptor | undefined> {
  const models = catalogModels(ctx);
  if (models.length === 0) throw new Error("Pi has no available models for this session");
  const selected = await ctx.ui.select("Translation model", models.map(modelLabel));
  if (!selected) return undefined;
  return models.find((model) => modelLabel(model) === selected);
}

async function chooseThinking(ctx: ExtensionContext, model: ModelDescriptor): Promise<string | undefined> {
  return ctx.ui.select("Thinking level", selectableThinkingLevels(model));
}

async function chooseWorkers(ctx: ExtensionContext): Promise<number | undefined> {
  while (true) {
    const value = await inputValue(ctx, "Worker count", "4");
    if (value === undefined) return undefined;
    if (/^[1-9]\d*$/.test(value)) return Number(value);
    ctx.ui.notify("Worker count must be a positive integer.", "warning");
  }
}

async function reviewScope(ctx: ExtensionContext, documents: DocumentInfo[]): Promise<string[] | undefined> {
  const selected: string[] = [];
  for (const document of documents) {
    const suggested = document.in_spine || document.is_nav ? "translate" : "keep";
    const description = [
      document.path,
      document.title ? `title: ${document.title}` : "no title",
      document.in_spine ? "in spine" : "outside spine",
      document.is_nav ? "navigation" : "",
      document.epub_types.length ? `types: ${document.epub_types.join(", ")}` : "",
      `recommended: ${suggested}`,
    ].filter(Boolean).join(" · ");
    const decision = await ctx.ui.select(description, ["translate", "keep"]);
    if (!decision) return undefined;
    if (decision === "translate") selected.push(document.path);
  }
  return selected;
}

async function analyzeJson<T>(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  model: string,
  thinking: string,
  prompt: string,
): Promise<T> {
  const response = await pi.exec("pi", [
    "--approve",
    "--no-session",
    "--no-context-files",
    "--no-builtin-tools",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--model",
    model,
    "--thinking",
    thinking,
    "-p",
    prompt,
  ], { signal: ctx.signal });
  if (response.code !== 0) {
    throw new Error(response.stderr.trim() || `analysis Pi exited with ${response.code}`);
  }
  return parseJsonResponse<T>(response.stdout);
}

function renderGlossary(analysis: GlossaryAnalysis): string {
  const entries = Array.isArray(analysis.entries) ? analysis.entries : [];
  const lines = entries
    .filter((entry) => entry && typeof entry.source === "string" && typeof entry.target === "string")
    .map((entry) => {
      const note = entry.note?.trim() ? ` # ${entry.note.trim()}` : "";
      return `${entry.source.trim()} = ${entry.target.trim()}${note}`;
    })
    .filter((line) => !line.startsWith(" = "));
  return lines.length > 0 ? `${lines.join("\n")}\n` : "# No fixed glossary entries were found.\n";
}

async function createGlossary(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  model: string,
  thinking: string,
  language: string,
  samples: unknown[],
  candidates: unknown[],
): Promise<string | undefined> {
  const prompt = [
    "Return JSON only with this exact shape: {\"entries\":[{\"source\":\"...\",\"target\":\"...\",\"note\":\"...\"}]}.",
    `Create a concise translation glossary for ${language}. Keep proper names, places, institutions, and recurring technical terms only.`,
    "Do not include generic words. Prefer one stable target rendering per source term.",
    `Candidate terms:\n${JSON.stringify(candidates)}`,
    `Sample text:\n${JSON.stringify(samples)}`,
  ].join("\n\n");
  const analysis = await analyzeJson<GlossaryAnalysis>(pi, ctx, model, thinking, prompt);
  const initial = renderGlossary(analysis);
  const edited = await ctx.ui.editor("Review glossary (source = target; # starts a note)", initial);
  if (edited === undefined) return undefined;
  const locked = await ctx.ui.confirm(
    "Lock glossary and start translation?",
    "Workers will use this exact glossary for every fragment in this run.",
  );
  return locked ? edited : undefined;
}

function safePart(value: string): string {
  return value.replace(/[^\p{L}\p{N}._-]+/gu, "-").replace(/^-+|-+$/g, "") || "book";
}

async function startWorkers(
  ctx: ExtensionContext,
  runDir: string,
  model: string,
  thinking: string,
  count: number,
): Promise<void> {
  for (let index = 0; index < count; index += 1) {
    spawnDetachedWorker(ctx, runDir, model, thinking, `seed-${index}-${Date.now()}`);
  }
}

async function runTranslationWizard(pi: ExtensionAPI, ctx: ExtensionContext): Promise<void> {
  const bookName = await chooseBook(ctx);
  if (!bookName) return;
  const model = await chooseModel(ctx);
  if (!model) return;
  const thinking = await chooseThinking(ctx, model);
  if (!thinking) return;
  const language = await inputValue(ctx, "Target language", "Vietnamese");
  if (!language) return;
  const languageCode = (await inputValue(ctx, "Target language BCP-47 code", "vi"))?.toLowerCase();
  if (!languageCode) return;
  const workers = await chooseWorkers(ctx);
  if (!workers) return;

  const source = resolve(ctx.cwd, "input", bookName);
  const documents = (await runHelper(pi, ctx, ["inspect-epub", "--epub", source])) as unknown as DocumentInfo[];
  const selectedPaths = await reviewScope(ctx, documents);
  if (!selectedPaths || selectedPaths.length === 0) {
    ctx.ui.notify("No XHTML document was selected; translation was not started.", "warning");
    return;
  }

  const sampleResponse = await runHelper(pi, ctx, [
    "sample-epub",
    "--epub",
    source,
    "--selected-json",
    JSON.stringify(selectedPaths),
    "--count",
    "6",
  ]);
  const samples = Array.isArray(sampleResponse) ? sampleResponse : [];
  const candidateResponse = await runHelper(pi, ctx, [
    "candidates",
    "--samples-json",
    JSON.stringify(samples.map((sample) => (sample as { sample?: string }).sample || "")),
  ]);
  const candidates = Array.isArray(candidateResponse) ? candidateResponse : [];
  const glossary = await createGlossary(pi, ctx, modelIdentifier(model), thinking, language, samples, candidates);
  if (glossary === undefined) return;

  const stem = basename(bookName, extname(bookName));
  const runId = `${safePart(stem)}-${safePart(languageCode)}-${Date.now()}`;
  const runDir = resolve(ctx.cwd, ".parallel-translate", runId);
  const output = resolve(ctx.cwd, "output", `${stem}-${safePart(languageCode)}.epub`);
  const config = {
    model: modelIdentifier(model),
    thinking,
    target_language: language,
    target_language_code: languageCode,
    workers,
    max_attempts: 3,
    output_path: output,
    glossary_text: glossary,
  };
  await runHelper(pi, ctx, [
    "prepare",
    "--source",
    source,
    "--run",
    runDir,
    "--config-json",
    JSON.stringify(config),
    "--selected-json",
    JSON.stringify(selectedPaths),
  ]);
  currentRunDir = runDir;
  await startWorkers(ctx, runDir, modelIdentifier(model), thinking, workers);
  pi.setSessionName(`EPUB: ${stem} → ${language}`);
  ctx.ui.notify(`Started ${bookName} with ${workers} stateless Pi workers.`, "info");
  await refreshProgress(pi, ctx, runDir);
}

async function runDirs(cwd: string): Promise<string[]> {
  const root = join(cwd, ".parallel-translate");
  let entries;
  try {
    entries = await readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }
  const candidates: { path: string; mtime: number }[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const path = join(root, entry.name);
    try {
      const metadata = await stat(join(path, "run.sqlite"));
      candidates.push({ path, mtime: metadata.mtimeMs });
    } catch {
      // Ignore incomplete directories left by a cancelled setup.
    }
  }
  return candidates.sort((left, right) => right.mtime - left.mtime).map((item) => item.path);
}

async function resolveRun(ctx: ExtensionContext, args: string): Promise<string | undefined> {
  const requested = args.trim();
  if (requested) {
    const path = resolve(ctx.cwd, requested);
    try {
      await stat(join(path, "run.sqlite"));
      return path;
    } catch {
      ctx.ui.notify(`Run not found: ${requested}`, "error");
      return undefined;
    }
  }
  if (currentRunDir) return currentRunDir;
  return (await runDirs(ctx.cwd))[0];
}

async function refreshProgress(pi: ExtensionAPI, ctx: ExtensionContext, runDir?: string): Promise<void> {
  if (!ctx.hasUI) return;
  const selected = runDir || currentRunDir || (await runDirs(ctx.cwd))[0];
  if (!selected) {
    ctx.ui.setWidget("epub-translate-progress", undefined);
    return;
  }
  try {
    const response = await runHelper(pi, ctx, ["summary", "--run", selected]);
    const summary = response as unknown as RunSummary;
    currentRunDir = selected;
    ctx.ui.setWidget("epub-translate-progress", formatProgress(summary, Date.now()));
  } catch {
    ctx.ui.setWidget("epub-translate-progress", undefined);
  }
}

function installProgressWidget(pi: ExtensionAPI, ctx: ExtensionContext): void {
  if (!ctx.hasUI) return;
  if (progressTimer) clearInterval(progressTimer);
  void refreshProgress(pi, ctx);
  progressTimer = setInterval(() => void refreshProgress(pi, ctx), 2_000);
  progressTimer.unref?.();
}

export default function (pi: ExtensionAPI): void {
  if (process.env.EPUB_TRANSLATE_RUN) registerWorkerTools(pi);

  pi.on("session_start", async (_event, ctx) => {
    installProgressWidget(pi, ctx);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    if (progressTimer) clearInterval(progressTimer);
    progressTimer = undefined;
    if (ctx.hasUI) ctx.ui.setWidget("epub-translate-progress", undefined);
  });

  pi.registerCommand("translate-epub", {
    description: "Review and start a stateless Pi EPUB translation run",
    handler: async (_args, ctx) => {
      if (ctx.mode !== "tui") {
        ctx.ui.notify("/translate-epub requires Pi TUI.", "error");
        return;
      }
      try {
        await runTranslationWizard(pi, ctx);
      } catch (error) {
        ctx.ui.notify(String(error), "error");
      }
    },
  });

  pi.registerCommand("epub-progress", {
    description: "Show the current EPUB translation progress",
    handler: async (args, ctx) => {
      const runDir = await resolveRun(ctx, args);
      if (!runDir) {
        ctx.ui.notify("No EPUB translation run exists.", "info");
        return;
      }
      await refreshProgress(pi, ctx, runDir);
      const response = await runHelper(pi, ctx, ["summary", "--run", runDir]);
      ctx.ui.notify(formatProgress(response as unknown as RunSummary, Date.now()).join("\n"), "info");
    },
  });

  pi.registerCommand("epub-cancel", {
    description: "Request cancellation of an EPUB translation run",
    handler: async (args, ctx) => {
      const runDir = await resolveRun(ctx, args);
      if (!runDir) {
        ctx.ui.notify("No EPUB translation run exists.", "info");
        return;
      }
      await runHelper(pi, ctx, ["cancel", "--run", runDir]);
      await refreshProgress(pi, ctx, runDir);
      ctx.ui.notify("Cancellation requested; active workers will stop at their next checkpoint.", "info");
    },
  });

  pi.registerCommand("epub-retry", {
    description: "Retry failed fragments of an EPUB translation run",
    handler: async (args, ctx) => {
      const runDir = await resolveRun(ctx, args);
      if (!runDir) {
        ctx.ui.notify("No EPUB translation run exists.", "info");
        return;
      }
      const response = await runHelper(pi, ctx, ["retry", "--run", runDir]);
      const summary = await runHelper(pi, ctx, ["summary", "--run", runDir]);
      const config = summary as unknown as { model?: string; thinking?: string; workers?: number };
      if (Number(response.reset || 0) > 0 && config.model) {
        await startWorkers(ctx, runDir, config.model, config.thinking || "high", Number(config.workers || 1));
      }
      await refreshProgress(pi, ctx, runDir);
      ctx.ui.notify(`Reset ${String(response.reset || 0)} failed fragment(s).`, "info");
    },
  });
}
