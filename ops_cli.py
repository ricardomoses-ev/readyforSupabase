#!/usr/bin/env python3
"""Unified runner for one-off operational scripts in this repository."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parent
DEFAULT_DOTENV = ROOT / "dashboard" / ".env"


def load_dotenv_if_exists(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


SCRIPT_MAP: Dict[str, str] = {
    "fetch-activity-type-names": "fetch_activity_type_names.py",
    "link-custom-activities": "link_custom_activities.py",
    "import-partners": "import_partners.py",
    "backfill-partner-referral": "backfill_partner_referral.py",
    "backfill-lead-magnet": "backfill_lead_magnet.py",
    "backfill-partner-display-names": "backfill_partner_display_names.py",
    "reset-password": "reset_password.py",
    "upsert-missing": "upsert_partner_missing_leads_and_custom_activities.py",
}


def run_script(script_name: str, passthrough_args: List[str]) -> int:
    script_path = ROOT / script_name
    if not script_path.exists():
        print(f"ERROR: Script not found: {script_path}")
        return 1
    cmd = [sys.executable, str(script_path), *passthrough_args]
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Unified operations CLI for one-off scripts.\n"
            "Use: python ops_cli.py <command> [-- <script args>]"
        )
    )
    parser.add_argument(
        "--dotenv-path",
        default=str(DEFAULT_DOTENV),
        help="Optional .env path to preload before running commands.",
    )
    parser.add_argument(
        "command",
        choices=sorted(SCRIPT_MAP.keys()) + ["list"],
        help="Operation to run, or 'list' to show commands.",
    )
    parser.add_argument(
        "script_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the underlying script.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    load_dotenv_if_exists(Path(args.dotenv_path))

    if args.command == "list":
        print("Available commands:")
        for cmd, script in sorted(SCRIPT_MAP.items()):
            print(f"  {cmd:<30} -> {script}")
        return 0

    script = SCRIPT_MAP[args.command]
    forwarded = list(args.script_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    return run_script(script, forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
