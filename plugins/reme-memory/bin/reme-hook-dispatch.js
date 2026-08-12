#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

function log(message) {
  try {
    const dir = path.join(os.homedir(), ".reme", "log");
    fs.mkdirSync(dir, { recursive: true });
    const stamp = new Date().toISOString().replace("T", " ").replace("Z", "");
    fs.appendFileSync(path.join(dir, "reme-plugin.log"), `${stamp} [dispatcher] ${message}\n`, "utf8");
  } catch (_) {
    // Best effort only.
  }
}

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || process.env.PLUGIN_ROOT || path.dirname(path.dirname(__filename));
const args = process.argv.slice(2);
let command;
let commandArgs;
if (process.platform === "win32") {
  command = path.join(pluginRoot, "bin", "reme-hook-launcher.exe");
  commandArgs = args;
} else {
  command = "/bin/sh";
  commandArgs = [path.join(pluginRoot, "bin", "reme-hook-launcher"), ...args];
}

let input = Buffer.alloc(0);
try {
  input = fs.readFileSync(0);
} catch (_) {
  input = Buffer.alloc(0);
}

try {
  const result = spawnSync(command, commandArgs, {
    input,
    encoding: null,
    windowsHide: true,
    shell: false,
    env: process.env,
    maxBuffer: 1024 * 1024,
  });
  if (result.stdout && result.stdout.length) {
    process.stdout.write(result.stdout);
  }
  if (result.error) {
    log(`spawn failed: ${result.error.message}`);
  } else if (typeof result.status === "number" && result.status !== 0) {
    log(`launcher exited with status ${result.status}`);
  }
} catch (error) {
  log(`dispatcher failed: ${error && error.message ? error.message : String(error)}`);
}
process.exit(0);
