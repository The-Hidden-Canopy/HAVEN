"""HAVEN's local verification contract: what a release candidate must pass.

There is no paid CI for this repository. `python scripts/verify.py` is the
substitute: it runs the exact test suite CI would, on the machine a
contributor already has, and prints the same thing a CI badge would --
Haven's commit, the interpreter and OS that ran it, and a pass/fail/skip
count -- with one addition a green CI badge does not give you: skips are
split into "this host lacks a real device/toolchain to prove this against"
(expected, on most machines) versus everything else (worth a second look).

The development rule this exists to support: a commit is not a release
candidate unless this passes. Nobody needs to be told that by hand, and
nobody needs a GitHub Actions bill to enforce it.

Usage:
    python scripts/verify.py              # human-readable summary
    python scripts/verify.py --json       # also write verification.json
    python scripts/verify.py --quiet      # summary only, no live pytest output
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Substring match against a skip's reason text (case-insensitive). These are
# the reasons this repo's own tests give for skipping when a real device,
# build toolchain, or optional runtime package isn't present on this host --
# not a gap in the test, a fact about the machine running it (see
# `tests/test_speech_native_audio.py`, `tests/test_bluetooth_fixture_backend.py`,
# `tests/test_bluetooth_winrt_backend.py`, `tests/test_models_backends_reference.py`).
# Update this list when a new environment-gated test is added with a reason
# that doesn't already match one of these substrings.
_ENVIRONMENT_SKIP_MARKERS = (
    "no windows audio",
    "no audio",
    "no microphone",
    "no speaker",
    "native audio hardware tests are opt-in",
    "no c compiler",
    "no msvc",
    "compiler on path",
    "windows sdk",
    "no bluetooth",
    "not installed here",
    "resolves its source from descriptor",
)


@dataclass
class SkipRecord:
    node_id: str
    reason: str
    environment_dependent: bool


@dataclass
class VerifyResult:
    ok: bool
    exit_code: int
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float
    skips: list[SkipRecord] = field(default_factory=list)

    @property
    def environment_skips(self) -> list[SkipRecord]:
        return [s for s in self.skips if s.environment_dependent]

    @property
    def other_skips(self) -> list[SkipRecord]:
        return [s for s in self.skips if not s.environment_dependent]


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _classify(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _ENVIRONMENT_SKIP_MARKERS)


def _run_pytest(*, quiet: bool) -> VerifyResult:
    with tempfile.TemporaryDirectory() as tmp:
        junit_path = Path(tmp) / "verify-junit.xml"
        args = [sys.executable, "-m", "pytest", f"--junitxml={junit_path}"]
        args.append("-q" if quiet else "-v")
        process = subprocess.run(args, cwd=_REPO_ROOT)

        tree = ET.parse(junit_path)
        root = tree.getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            raise RuntimeError("pytest produced no readable junit report")

        total = int(suite.get("tests", 0))
        failures = int(suite.get("failures", 0))
        errors = int(suite.get("errors", 0))
        skipped = int(suite.get("skipped", 0))
        duration = float(suite.get("time", 0.0))
        passed = total - failures - errors - skipped

        skips: list[SkipRecord] = []
        for case in suite.iter("testcase"):
            skip_el = case.find("skipped")
            if skip_el is None:
                continue
            reason = skip_el.get("message") or (skip_el.text or "").strip()
            node_id = f"{case.get('classname', '')}::{case.get('name', '')}"
            skips.append(SkipRecord(node_id=node_id, reason=reason, environment_dependent=_classify(reason)))

    return VerifyResult(
        ok=process.returncode == 0,
        exit_code=process.returncode,
        total=total,
        passed=passed,
        failed=failures,
        errors=errors,
        skipped=skipped,
        duration_seconds=duration,
        skips=skips,
    )


def _print_summary(result: VerifyResult, *, commit: str | None, dirty: bool | None) -> None:
    print()
    print("=" * 70)
    print("HAVEN local verification")
    print("=" * 70)
    print(f"commit:        {commit or 'unknown (not a git checkout?)'}" + (" (dirty)" if dirty else ""))
    print(f"python:        {platform.python_version()} ({sys.implementation.name})")
    print(f"os:            {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"duration:      {result.duration_seconds:.1f}s")
    print("-" * 70)
    print(f"total:         {result.total}")
    print(f"passed:        {result.passed}")
    print(f"failed:        {result.failed}")
    print(f"errors:        {result.errors}")
    print(f"skipped:       {result.skipped}  "
          f"(environment-dependent: {len(result.environment_skips)}, other: {len(result.other_skips)})")
    if result.environment_skips:
        print()
        print("environment-dependent skips (expected: missing device, toolchain, or optional runtime package):")
        for skip in result.environment_skips:
            print(f"  - {skip.node_id}: {skip.reason}")
    if result.other_skips:
        print()
        print("OTHER SKIPS -- worth a second look, not explained by this host's hardware/toolchain/packages:")
        for skip in result.other_skips:
            print(f"  - {skip.node_id}: {skip.reason}")
    print("-" * 70)
    if result.ok:
        print("VERIFY PASSED -- this commit is a release candidate.")
    else:
        print("VERIFY FAILED -- this commit is NOT a release candidate.")
    print("=" * 70)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="also write verification.json to the repo root")
    parser.add_argument("--quiet", action="store_true", help="run pytest with -q instead of -v")
    args = parser.parse_args()

    commit = _git_commit()
    dirty = _git_dirty()
    result = _run_pytest(quiet=args.quiet)
    _print_summary(result, commit=commit, dirty=dirty)

    if args.json:
        payload = {
            "ok": result.ok,
            "commit": commit,
            "dirty": dirty,
            "python_version": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "skipped": result.skipped,
            "environment_dependent_skips": [s.__dict__ for s in result.environment_skips],
            "other_skips": [s.__dict__ for s in result.other_skips],
            "duration_seconds": result.duration_seconds,
        }
        out_path = _REPO_ROOT / "verification.json"
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
