#!/usr/bin/env node
"use strict";

// Cross-platform Claude Code dispatcher.
// Claude Code invokes this file in exec form (`command` + `args`), so no Bash,
// No host shell is selected by the plugin. The
// dispatcher only chooses the already-tested platform launcher and preserves
// hook stdin as raw bytes.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

function log(status, detail = "") {
  try {
    const logDir = path.join(os.homedir(), ".reme", "log");
    fs.mkdirSync(logDir, { recursive: true });
    const stamp = new Date().toISOString().replace("T", " ").replace("Z", "");
    const suffix = detail ? ` ${detail}` : "";
    fs.appendFileSync(
      path.join(logDir, "reme-plugin.log"),
      `${stamp} [dispatcher] ${status}${suffix}\n`,
      { encoding: "utf8" },
    );
  } catch (_) {
    // Memory recording is best-effort; diagnostics must never break the host.
  }
}

const mode = process.argv[2] || "";
if (mode !== "--stop") {
  log("invalid-mode", `mode=${JSON.stringify(mode)}`);
  process.exit(0);
}

const pluginRoot =
  process.env.CLAUDE_PLUGIN_ROOT ||
  process.env.PLUGIN_ROOT ||
  path.resolve(__dirname, "..");

let command;
let args;
if (process.platform === "win32") {
  command = path.join(pluginRoot, "bin", "reme-hook-launcher.exe");
  args = [mode];
} else {
  // Use the platform's POSIX shell only to execute the portable launcher; the
  // Claude hook itself remains exec-form and never depends on Claude's shell.
  command = "/bin/sh";
  args = [path.join(pluginRoot, "bin", "reme-hook-launcher"), mode];
}

let child;
try {
  child = spawn(command, args, {
    cwd: pluginRoot,
    env: process.env,
    shell: false,
    windowsHide: true,
    stdio: ["pipe", "ignore", "ignore"],
  });
} catch (error) {
  log("spawn-error", String(error));
  process.exit(0);
}

let finished = false;
function finish() {
  if (finished) return;
  finished = true;
  process.exit(0);
}

child.on("error", (error) => {
  log("child-error", String(error));
  finish();
});
child.on("close", (code, signal) => {
  if (code !== 0) {
    log("child-exit", `code=${String(code)} signal=${String(signal || "")}`);
  }
  finish();
});
child.stdin.on("error", (error) => {
  // EPIPE only means the child exited before consuming all stdin.
  if (error && error.code !== "EPIPE") {
    log("stdin-error", String(error));
  }
});
process.stdin.on("error", (error) => {
  log("hook-stdin-error", String(error));
  try {
    child.stdin.end();
  } catch (_) {}
});
process.stdin.pipe(child.stdin);
