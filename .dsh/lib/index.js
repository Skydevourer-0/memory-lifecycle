import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

export const name = "memory-lifecycle-dsh";
export const inject = [];

const SOURCE = { kind: "plugin", plugin: "memory-lifecycle-dsh" };

function userMessage(text) {
  return { id: randomUUID(), role: "user", content: [{ type: "text", text }], source: SOURCE };
}
const MEMORY_SYNC_PY = join(homedir(), ".cc-switch", "skills", "memory-lifecycle", "scripts", "memory-sync.py");

function run(file, args, cwd) {
  return new Promise((resolve, reject) => {
    execFile(file, args, { timeout: 8000, maxBuffer: 1024 * 1024, windowsHide: true, cwd }, (error, stdout) => {
      if (error) reject(error);
      else resolve((stdout || "").trim());
    });
  });
}

function extractContext(raw) {
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw);
    const ac = parsed?.hookSpecificOutput?.additionalContext;
    if (typeof ac === "string") return ac.trim();
  } catch {}
  return raw.trim();
}

export function apply(ctx) {
  ctx.on("agent/session-start", ({ agent }) => {
    const cwd = agent?.session?.header?.cwd ?? process.cwd();
    run("python", [MEMORY_SYNC_PY, "session-start"], cwd)
      .then((raw) => {
        const text = extractContext(raw);
        if (!text) return;
        agent.inject(userMessage(text));
      })
      .catch((error) => {
        ctx.logger.warn("memory-lifecycle-dsh: session-start hook failed: " + String(error));
      });
  });
}
