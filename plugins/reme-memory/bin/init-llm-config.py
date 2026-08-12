#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "config" / "llm.example.json"
target = Path.home() / ".reme" / "config" / "llm.json"
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    print(f"Configuration already exists: {target}")
else:
    shutil.copyfile(source, target)
    if os.name != "nt":
        target.chmod(0o600)
    print(f"Created: {target}")
    print("Edit default.base_url, default.api_key, and default.model before enabling LLM features.")
    print("recall_gate inherits missing values from default.")
