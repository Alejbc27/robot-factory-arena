"""One-shot export CLI (Unit B, tasks 1/5).

The only supported command is::

    python3 -m observability export --artifacts-root ROOT --langfuse-url URL [--include-text]

Credentials come from the environment only (``LANGFUSE_PUBLIC_KEY``/
``LANGFUSE_SECRET_KEY``); no credential option exists and supplied values are
never echoed. Exit codes: ``2`` for usage/config errors (argparse, missing
option, invalid URL, missing credentials) without touching any artifact, ``0``
for a complete run with no skipped items, ``1`` when any item is skipped or
fails or the endpoint guard rejects the run. Stdout carries exactly the
redacted machine-readable report; stderr carries counts, stable reason codes,
and safe paths only.
"""

import argparse
import os
import sys
import urllib.parse

from . import langfuse_http
from .exporter import run_export

ENV_PUBLIC_KEY = "LANGFUSE_PUBLIC_KEY"
ENV_SECRET_KEY = "LANGFUSE_SECRET_KEY"
URL_SCHEMES = frozenset({"http", "https"})


class _Parser(argparse.ArgumentParser):
    """Parser whose errors go to the injectable stream and always exit 2."""

    def __init__(self, *args, stderr=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._stream = stderr if stderr is not None else sys.stderr

    def error(self, message):
        self.print_usage(self._stream)
        self._stream.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(2)


def build_parser(stderr=None):
    parser = _Parser(
        prog="python3 -m observability",
        description="One-shot sealed-evidence export to self-hosted Langfuse.",
        stderr=stderr,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="export one artifact root (one-shot)")
    export.add_argument("--artifacts-root", required=True)
    export.add_argument("--langfuse-url", required=True)
    export.add_argument(
        "--include-text",
        action="store_true",
        help="opt in to eligible response/tool-argument text (still secret-scanned)",
    )
    return parser


def _validated_url(value, parser):
    """http(s) URL with a host and no embedded credentials; never echoed.

    Explicit empty query/fragment delimiters, malformed IPv6 hosts, and
    malformed ports all exit 2 before any processing; the offending value is
    never echoed.
    """
    try:
        parts = urllib.parse.urlsplit(value)
        hostname = parts.hostname
        _ = parts.port  # evaluated to fail closed on malformed ports (ValueError)
    except ValueError:
        parser.error(
            "--langfuse-url must be an http(s) URL without embedded credentials"
        )
    if (
        parts.scheme not in URL_SCHEMES
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or "?" in value
        or "#" in value
    ):
        parser.error(
            "--langfuse-url must be an http(s) URL without embedded credentials"
        )


def _credentials(environ, parser):
    public = environ.get(ENV_PUBLIC_KEY, "")
    secret = environ.get(ENV_SECRET_KEY, "")
    if not public or not secret:
        parser.error(
            f"{ENV_PUBLIC_KEY} and {ENV_SECRET_KEY} must be set in the environment"
        )
    return public, secret


def _report_line(report):
    counts = report.counts
    if report.error is not None:
        return f"export aborted: {report.error}\n"
    if counts["skipped"]:
        return (
            f"export incomplete: failed={counts['failed']} "
            f"skipped={counts['skipped']}\n"
        )
    return (
        f"export ok: processed={counts['processed']} "
        f"accepted_items={counts['accepted_items']}\n"
    )


def main(argv=None, *, environ=None, stdout=None, stderr=None):
    """Run one CLI invocation and return the process exit code."""
    parser = build_parser(stderr)
    args = parser.parse_args(argv)
    if not args.artifacts_root:
        parser.error("--artifacts-root must be a non-empty path")
    _validated_url(args.langfuse_url, parser)
    public_key, secret_key = _credentials(
        os.environ if environ is None else environ, parser
    )
    client = langfuse_http.LangfuseClient(args.langfuse_url, public_key, secret_key)
    result = run_export(args.artifacts_root, client, include_text=args.include_text)
    stream = sys.stdout if stdout is None else stdout
    stream.write(result.render() + "\n")
    err = sys.stderr if stderr is None else stderr
    err.write(_report_line(result))
    if result.error is None and result.counts["skipped"]:
        for skip in result.skipped:
            err.write(f"skipped: {skip.reason} {skip.path}\n")
    if result.error is not None or result.counts["skipped"]:
        return 1
    return 0
