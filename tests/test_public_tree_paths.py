"""Shipped docs and source carry no local machine paths or links outside the repository.

docs/public-private-boundary.md keeps filesystem paths private. Plugin documentation
names its plugins and their contracts, but never a developer's checkout location.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ("README.md", "TASKS.md", "docs", "haven", "native", "scripts")
TEXT_SUFFIXES = {".md", ".py", ".cs", ".xaml", ".js", ".html", ".css", ".json", ".ps1", ".bat", ".vbs", ".kt", ".txt"}
SKIPPED_DIRS = {"bin", "obj", "node_modules", "__pycache__", ".gradle", "build"}

MACHINE_PATH = re.compile(r"\b[A-Za-z]:[\\/](?:HiddenCanopy|Users[\\/](?!you\b|<))", re.IGNORECASE)
ESCAPING_LINK = re.compile(r"\]\((?:\.\./){2,}")


def _shipped_files():
    for entry in SHIPPED:
        path = ROOT / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if (
                    candidate.is_file()
                    and candidate.suffix.lower() in TEXT_SUFFIXES
                    and not SKIPPED_DIRS.intersection(candidate.relative_to(ROOT).parts)
                ):
                    yield candidate


def test_shipped_files_have_no_local_machine_paths_or_out_of_repo_links() -> None:
    offenders = []
    for path in _shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if MACHINE_PATH.search(line) or ESCAPING_LINK.search(line):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert offenders == []
