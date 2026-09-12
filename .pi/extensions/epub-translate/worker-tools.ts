import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { buildWorkerCommand, WORKER_TOOL_NAMES } from "./worker-command.ts";

interface HelperResponse {
  [key: string]: unknown;
}

interface ClaimedPayload {
  id: number;
  lease_token: string;
  model: string;
  thinking: string;
  source: string;
}

interface Lease {
  jobId: number;
  leaseToken: string;
}

const HEARTBEAT_MS = 30_000;

export function runDirectory(): string {
  const value = process.env.EPUB_TRANSLATE_RUN;
  if (!value) throw new Error("EPUB_TRANSLATE_RUN is not set");
  return resolve(value);
}

export function workerId(): string {
  return process.env.EPUB_TRANSLATE_WORKER_ID || randomUUID();
}

function helperPath(ctx: ExtensionContext): string {
  return resolve(ctx.cwd, ".pi/extensions/epub-translate/run_store.py");
}

export async function runHelper(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  args: string[],
  signal?: AbortSignal,
): Promise<HelperResponse> {
  const completed = await pi.exec("python3", [helperPath(ctx), ...args], { signal });
  const output = completed.stdout.trim();
  let payload: HelperResponse;
  try {
    payload = output ? (JSON.parse(output) as HelperResponse) : {};
  } catch (error) {
    throw new Error(`run-store returned invalid JSON: ${output || String(error)}`);
  }
  if (completed.code !== 0 || typeof payload.error === "string") {
    throw new Error(String(payload.error || completed.stderr || `run-store exited ${completed.code}`));
  }
  return payload;
}

export function spawnDetachedWorker(
  ctx: ExtensionContext,
  runDir: string,
  model: string,
  thinking: string,
  replacementId: string,
): void {
  const command = buildWorkerCommand(runDir, model, thinking);
  const child = spawn(command.command, command.args, {
    cwd: ctx.cwd,
    detached: true,
    stdio: "ignore",
    env: {
      ...process.env,
      EPUB_TRANSLATE_RUN: runDir,
      EPUB_TRANSLATE_WORKER_ID: replacementId,
      EPUB_TRANSLATE_MODEL: model,
      EPUB_TRANSLATE_THINKING: thinking,
    },
  });
  child.unref();
}

function translatedTempPath(runDir: string, worker: string, jobId: number): string {
  return join(runDir, ".worker-tmp", `${worker}-${jobId}-${randomUUID()}.xhtml`);
}

export function registerWorkerTools(pi: ExtensionAPI): void {
  let activeLease: Lease | undefined;
  let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
  let heartbeatRunning = false;
  const currentWorkerId = workerId();

  const stopHeartbeat = () => {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = undefined;
    activeLease = undefined;
  };

  const startHeartbeat = (lease: Lease, ctx: ExtensionContext) => {
    stopHeartbeat();
    activeLease = lease;
    heartbeatTimer = setInterval(async () => {
      if (!activeLease || heartbeatRunning) return;
      heartbeatRunning = true;
      try {
        await runHelper(pi, ctx, [
          "heartbeat",
          "--run",
          runDirectory(),
          "--job-id",
          String(activeLease.jobId),
          "--worker-id",
          currentWorkerId,
          "--lease-token",
          activeLease.leaseToken,
        ]);
      } catch {
        // The next completion is fenced by the same token. A stale worker
        // must never write a result after another worker reclaims the job.
      } finally {
        heartbeatRunning = false;
      }
    }, HEARTBEAT_MS);
    heartbeatTimer.unref?.();
  };

  pi.on("session_shutdown", async () => {
    stopHeartbeat();
  });

  pi.registerTool({
    name: WORKER_TOOL_NAMES[0],
    label: "Claim EPUB fragment",
    description: "Atomically claim one EPUB fragment from the current translation run.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal, _onUpdate, ctx) {
      if (activeLease) throw new Error("worker already has a claimed fragment");
      const response = await runHelper(pi, ctx, [
        "claim",
        "--run",
        runDirectory(),
        "--worker-id",
        currentWorkerId,
      ], signal);
      const job = response.job as ClaimedPayload | null | undefined;
      if (!job) {
        return {
          content: [{ type: "text", text: "No EPUB fragment is available." }],
          details: { action: "empty" },
          terminate: true,
        };
      }
      startHeartbeat({ jobId: job.id, leaseToken: job.lease_token }, ctx);
      return {
        content: [{ type: "text", text: JSON.stringify(job) }],
        details: { action: "claimed", jobId: job.id },
      };
    },
  });

  pi.registerTool({
    name: WORKER_TOOL_NAMES[1],
    label: "Complete EPUB fragment",
    description: "Validate and atomically publish a translated EPUB fragment.",
    parameters: Type.Object({
      jobId: Type.Integer({ minimum: 1 }),
      leaseToken: Type.String({ minLength: 1 }),
      translated: Type.String({ minLength: 1 }),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const lease = activeLease;
      if (!lease || lease.jobId !== params.jobId || lease.leaseToken !== params.leaseToken) {
        throw new Error("fragment lease does not belong to this worker");
      }
      const runDir = runDirectory();
      const temporary = translatedTempPath(runDir, currentWorkerId, params.jobId);
      await mkdir(join(runDir, ".worker-tmp"), { recursive: true });
      try {
        await writeFile(temporary, params.translated, "utf8");
        const response = await runHelper(pi, ctx, [
          "complete",
          "--run",
          runDir,
          "--job-id",
          String(params.jobId),
          "--worker-id",
          currentWorkerId,
          "--lease-token",
          params.leaseToken,
          "--translated-file",
          temporary,
        ], signal);
        if (response.accepted !== true) throw new Error("fragment lease was fenced by another worker");
        return {
          content: [{ type: "text", text: `Completed EPUB fragment ${params.jobId}.` }],
          details: response,
        };
      } catch (error) {
        try {
          await runHelper(pi, ctx, [
            "fail",
            "--run",
            runDir,
            "--job-id",
            String(params.jobId),
            "--worker-id",
            currentWorkerId,
            "--lease-token",
            params.leaseToken,
            "--error",
            String(error),
          ], signal);
        } catch {
          // A lost lease is already fenced; do not hide the original failure.
        }
        throw error;
      } finally {
        stopHeartbeat();
        await rm(temporary, { force: true });
      }
    },
  });

  pi.registerTool({
    name: WORKER_TOOL_NAMES[2],
    label: "Spawn EPUB worker",
    description: "Continue the stateless EPUB worker chain or report merge ownership.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal, _onUpdate, ctx) {
      const runDir = runDirectory();
      const summary = await runHelper(pi, ctx, ["summary", "--run", runDir], signal);
      if (summary.cancelRequested === true) {
        return {
          content: [{ type: "text", text: "Cancellation was requested; worker chain stopped." }],
          details: { action: "cancelled" },
          terminate: true,
        };
      }
      if (summary.state === "failed") {
        return {
          content: [{ type: "text", text: "The run has terminal failures; no replacement was spawned." }],
          details: { action: "failed" },
          terminate: true,
        };
      }
      if (summary.done === summary.total && summary.total > 0) {
        const merge = await runHelper(pi, ctx, [
          "claim-merge",
          "--run",
          runDir,
          "--worker-id",
          currentWorkerId,
        ], signal);
        if (merge.claimed === true) {
          return {
            content: [{ type: "text", text: "This worker owns the final merge." }],
            details: { action: "merge" },
          };
        }
        return {
          content: [{ type: "text", text: "Another worker owns the final merge; this worker exits." }],
          details: { action: "wait" },
          terminate: true,
        };
      }
      const retryable = Number(summary.retryable || 0);
      if (Number(summary.pending || 0) > 0 || Number(summary.leased || 0) > 0 || retryable > 0) {
        const model = process.env.EPUB_TRANSLATE_MODEL || "";
        const thinking = process.env.EPUB_TRANSLATE_THINKING || "high";
        if (!model) throw new Error("EPUB_TRANSLATE_MODEL is not set");
        spawnDetachedWorker(ctx, runDir, model, thinking, randomUUID());
        return {
          content: [{ type: "text", text: "Spawned a fresh stateless EPUB worker." }],
          details: { action: "spawned" },
          terminate: true,
        };
      }
      await runHelper(pi, ctx, [
        "fail-run",
        "--run",
        runDir,
        "--error",
        "No retryable EPUB work remains.",
      ], signal);
      return {
        content: [{ type: "text", text: "No retryable EPUB work remains; run marked failed." }],
        details: { action: "failed" },
        terminate: true,
      };
    },
  });

  pi.registerTool({
    name: WORKER_TOOL_NAMES[3],
    label: "Merge translated EPUB",
    description: "Build and verify the final EPUB after this worker owns the merge lease.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal, _onUpdate, ctx) {
      const response = await runHelper(pi, ctx, ["merge", "--run", runDirectory()], signal);
      return {
        content: [{ type: "text", text: `Built ${String(response.output)}.` }],
        details: response,
        terminate: true,
      };
    },
  });
}
