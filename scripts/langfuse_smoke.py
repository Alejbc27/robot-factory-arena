#!/usr/bin/env python3
"""Thin local/CI entrypoint for the ephemeral Langfuse v3.114.0 PR smoke.

Operational, non-secret options only: Compose-file path, report path, and a
global-deadline override. No Langfuse-version option and no credential option
exists; CI invokes this script without arguments. A ``--report`` path is
opt-in and non-secret; the runtime writes those exact final bytes itself and
scans them after write (fail-closed on exposure). Without it the report is
only printed to stdout, so the CI default never writes a file. Exit codes:
0 overall PASS, 1 smoke/cleanup failure (red check), 2 usage/configuration
errors before any mutation. The runtime lives in ``observability.smoke``.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import replace  # noqa: E402

import observability.smoke as smoke  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(
        description="Ephemeral Langfuse v3.114.0 PR smoke (CI-LF-01..05)."
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=ROOT / "observability" / "docker-compose.yml",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout_seconds < 300 or args.timeout_seconds > 7200:
        parser.error("--timeout-seconds must be between 300 and 7200")
    if not args.compose_file.is_file():
        parser.error(f"compose file not found: {args.compose_file.name}")
    config = smoke.SmokeConfig(
        compose_file=args.compose_file,
        source_env=dict(os.environ),
        report_path=args.report,
        bounds=replace(smoke.Bounds(), global_deadline_s=args.timeout_seconds),
    )
    report, text = smoke.run_smoke(config)
    print(text, flush=True)
    return smoke.exit_code_for(report)


if __name__ == "__main__":
    raise SystemExit(main())
