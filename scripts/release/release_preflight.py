#!/usr/bin/env python3
"""Fail a production release when packaged files contain local-only material."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


RULES = {
    "private_or_local_url": re.compile(r"https?://(?:127\.0\.0\.1|localhost|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", re.I),
    "developer_absolute_path": re.compile(r"(?:/Users/|/home/[^/$\s]+/|[A-Z]:\\Users\\)"),
    "literal_secret": re.compile(r"(?i)(?:api[_-]?key|password|token)\s*[:=]\s*['\"]?(?!\$\{|<|\*|\s*$)[A-Za-z0-9_\-]{12,}"),
}
TEXT_SUFFIXES = {".yaml", ".yml", ".json", ".js", ".css", ".html", ".md", ".txt", ".py"}


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in {"node_modules", ".git", "test-results", "data", "tests"} for part in path.parts):
            continue
        if path.name.startswith("chunk-vendors") or "pdf.worker" in path.name:
            # Third-party bundles may contain inert documentation URLs.  They
            # are lockfile-pinned and cannot carry deployment configuration.
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in RULES.items():
            if name == "literal_secret" and path.suffix.lower() not in {".yaml", ".yml", ".json", ".env"} and path.name != "runtime-config.js":
                continue
            if pattern.search(text):
                findings.append(f"{name}: {path.relative_to(root)}")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    findings = scan(args.root.resolve())
    if findings:
        raise SystemExit("release preflight failed:\n" + "\n".join(findings))
    print("release preflight passed")


if __name__ == "__main__":
    main()
