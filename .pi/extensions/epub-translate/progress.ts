export interface RunSummary {
  state: string;
  mergeState: string;
  total: number;
  done: number;
  leased: number;
  failed: number;
  pending: number;
  startedAt: number;
  cancelRequested: boolean;
  outputPath?: string | null;
  error?: string | null;
}

function formatElapsed(startedAt: number, nowMs: number): string {
  const elapsedSeconds = Math.max(0, Math.floor(nowMs / 1000 - startedAt));
  const hours = Math.floor(elapsedSeconds / 3600);
  const minutes = Math.floor((elapsedSeconds % 3600) / 60);
  const seconds = elapsedSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function formatProgress(summary: RunSummary, nowMs: number): string[] {
  if (summary.state === "completed") {
    return [`EPUB complete · ${summary.done}/${summary.total}`];
  }
  if (summary.state === "failed") {
    return [`EPUB needs retry · ${summary.done}/${summary.total} · ${summary.failed} failed`];
  }
  if (summary.cancelRequested) {
    return [`EPUB cancellation requested · ${summary.done}/${summary.total}`];
  }
  if (summary.mergeState === "leased") {
    return [`EPUB merging · ${summary.done}/${summary.total}`];
  }
  return [
    `EPUB translating · ${summary.done}/${summary.total} complete · ${summary.leased} active · ${summary.failed} failed · ${formatElapsed(summary.startedAt, nowMs)}`,
  ];
}
