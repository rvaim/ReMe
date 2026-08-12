#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PLUGIN_ROOT / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from reme_plugin.common import emit_additional_context, log, read_stdin_json
from reme_plugin.gate import GateConfigurationError, decide, load_config
from reme_plugin.memory import (
    capture_user_prompt,
    dispatch_writer,
    queue_stop,
    recent_user_prompts,
    run_writer,
)
from reme_plugin.recall import recall_context


def handle_user_prompt(host: str, payload: dict) -> None:
    session_id = str(payload.get("session_id") or "")
    prompt = payload.get("prompt")
    previous = recent_user_prompts(host, session_id)
    capture_user_prompt(host, payload)
    if not isinstance(prompt, str) or not prompt.strip():
        return
    try:
        config = load_config()
    except GateConfigurationError as exc:
        log("gate", "config-invalid", str(exc), session_id)
        return
    if config is None:
        return
    decision = decide(prompt, previous, config, session_id=session_id)
    if decision.decision != "recall" or not decision.query:
        return
    context = recall_context(prompt, decision.query, config, session_id=session_id)
    emit_additional_context(context)


def handle_stop(host: str, payload: dict) -> None:
    if queue_stop(host, payload):
        dispatch_writer()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", choices=("claude", "codex"))
    event = parser.add_mutually_exclusive_group(required=True)
    event.add_argument("--user-prompt", action="store_true")
    event.add_argument("--stop", action="store_true")
    event.add_argument("--worker", action="store_true")
    args = parser.parse_args(argv)

    if args.worker:
        run_writer()
        return
    if not args.host:
        log("hook", "missing-host")
        return

    payload = read_stdin_json()
    session_id = str(payload.get("session_id") or "")
    try:
        if args.user_prompt:
            handle_user_prompt(args.host, payload)
        elif args.stop:
            handle_stop(args.host, payload)
    except Exception as exc:
        log("hook", "failed-open", repr(exc), session_id)


if __name__ == "__main__":
    main()
