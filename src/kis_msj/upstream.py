"""Check the local Korea Investment open-trading-api reference repository."""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_REFERENCE_REPO = Path(r"C:\MSJ\open-trading-api")
WATCHED_PATHS = (
    "examples_user",
    "kis_devlp.yaml",
    "pyproject.toml",
    "requirements.txt",
)


@dataclass(frozen=True)
class UpstreamStatus:
    repo_path: Path
    branch: str
    remote_url: str
    local_head: str
    remote_ref: str
    remote_head: str
    ahead: int
    behind: int
    changed_files: tuple[str, ...]

    @property
    def has_updates(self) -> bool:
        return self.behind > 0 or bool(self.changed_files)


def _git(repo_path: Path, *args: str, timeout: int = 30) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.stdout.strip()


def _remote_head_ref(repo_path: Path) -> str:
    try:
        return _git(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD", "--short")
    except subprocess.CalledProcessError:
        return "origin/main"


def check_open_trading_api_status(repo_path: Path = DEFAULT_REFERENCE_REPO, *, fetch: bool = False) -> UpstreamStatus:
    """Return local-vs-origin status for a reference ``open-trading-api`` clone."""

    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"{repo_path} is not a git repository")

    if fetch:
        _git(repo_path, "fetch", "--prune", "origin", timeout=120)

    branch = _git(repo_path, "branch", "--show-current") or "(detached)"
    remote_url = _git(repo_path, "remote", "get-url", "origin")
    local_head = _git(repo_path, "rev-parse", "--short", "HEAD")
    remote_ref = _remote_head_ref(repo_path)
    remote_head = _git(repo_path, "rev-parse", "--short", remote_ref)
    counts = _git(repo_path, "rev-list", "--left-right", "--count", f"HEAD...{remote_ref}")
    ahead_text, behind_text = counts.split()

    changed = _git(repo_path, "diff", "--name-only", f"HEAD...{remote_ref}", "--", *WATCHED_PATHS)
    changed_files = tuple(line for line in changed.splitlines() if line)

    return UpstreamStatus(
        repo_path=repo_path,
        branch=branch,
        remote_url=remote_url,
        local_head=local_head,
        remote_ref=remote_ref,
        remote_head=remote_head,
        ahead=int(ahead_text),
        behind=int(behind_text),
        changed_files=changed_files,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check local open-trading-api reference repo status.")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REFERENCE_REPO, help="Path to open-trading-api clone")
    parser.add_argument("--fetch", action="store_true", help="Run git fetch before comparing")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = check_open_trading_api_status(args.repo, fetch=args.fetch)

    print(f"repo: {status.repo_path}")
    print(f"remote: {status.remote_url}")
    print(f"branch: {status.branch}")
    print(f"local: {status.local_head}")
    print(f"remote: {status.remote_ref} ({status.remote_head})")
    print(f"ahead: {status.ahead}")
    print(f"behind: {status.behind}")

    if status.changed_files:
        print("watched changes:")
        for path in status.changed_files:
            print(f"  - {path}")
    else:
        print("watched changes: none")

    return 1 if status.has_updates else 0


if __name__ == "__main__":
    raise SystemExit(main())
