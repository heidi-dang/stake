// Small, stable activity event contract and safe parser.
// This file is intentionally plain JavaScript so it can be imported
// by both server and UI without requiring a TypeScript build step.

const VALID_PHASES = new Set([
  "analyzing",
  "planning",
  "running_tests",
  "installing",
  "building",
  "executing",
  "finalizing",
  "done",
  "error",
]);

// Sanitize an incoming raw event. Always returns a plain object with
// guaranteed shapes for fields used by the UI. Unknown fields are ignored.
function parseActivityEvent(raw) {
  if (!raw || typeof raw !== "object") return null;

  const phase = String(raw.phase || "");
  if (!VALID_PHASES.has(phase)) return null;

  const ts = raw.ts ? Number(raw.ts) : Date.now();
  const message = raw.message ? String(raw.message) : "";

  const terminal = raw.terminal && typeof raw.terminal === "object" ? {
    cmd: raw.terminal.cmd ? String(raw.terminal.cmd) : undefined,
    stdout: raw.terminal.stdout ? String(raw.terminal.stdout) : undefined,
    stderr: raw.terminal.stderr ? String(raw.terminal.stderr) : undefined,
    exitCode: typeof raw.terminal.exitCode === "number" ? raw.terminal.exitCode : undefined,
  } : undefined;

  const runId = raw.runId ? String(raw.runId) : undefined;
  const turnId = raw.turnId ? String(raw.turnId) : undefined;

  return {
    phase,
    message,
    ts,
    terminal,
    runId,
    turnId,
  };
}

module.exports = { VALID_PHASES, parseActivityEvent };
