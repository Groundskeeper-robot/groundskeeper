"""Check what this branch changes, before review, against the AGENTS.md rules that a whole-repo check can't see: no
added test skips and no edit to tests/test_guards.py. scripts/check.py runs it.

    python scripts/check_diff.py               # against upstream/main, origin/main or github/main
    GK_BASE=my-base python scripts/check_diff.py

It compares the working tree (committed, staged, unstaged and new files) with the point where the branch left the
first of those that shares its history. With none (a shallow CI checkout), it says so and passes. A maintainer
changing a guard test because the issue asks for exactly that sets GK_GUARD_CHANGE_OK=1."""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASES = ("upstream/main", "origin/main", "github/main")
GUARDS = "tests/test_guards.py"
SKIP = re.compile(r"\bpytest\.(?:skip|xfail|importorskip)\(|\bmark\.(?:skip|skipif|xfail)\b"
                  r"|^\s*from pytest import .*\b(?:skip|xfail|importorskip)\b")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def merge_base() -> tuple[str, str] | None:
    """(ref, commit) of the first base that shares history with HEAD, or None."""
    for ref in [os.environ["GK_BASE"]] if os.environ.get("GK_BASE") else BASES:
        r = git("merge-base", "HEAD", ref)
        if r.returncode == 0 and r.stdout.strip():
            return ref, r.stdout.strip()
    return None


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Path -> [(line number, text)] for every line a unified diff (-U0) adds."""
    out: dict[str, list[tuple[int, str]]] = {}
    path, n, header = None, 0, False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path:
                out.setdefault(path, [])
        elif line.startswith("@@"):
            header = False
            m = re.match(r"@@ -\S+ \+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif path and not header and line.startswith("+"):
            out[path].append((n, line[1:]))
            n += 1
    return out


def problems(changed: dict[str, list[tuple[int, str]]], guard_change_ok: bool = False) -> list[str]:
    """The rule breaks in the changed paths (path -> added lines; a path with no added lines was only cut down)."""
    out = []
    if GUARDS in changed and not guard_change_ok:
        out.append(f"{GUARDS}: changed. It encodes the AGENTS.md rules: a failing guard test means the change is "
                   "wrong. Only when the issue asks for exactly this, say so in the PR "
                   "(maintainer: GK_GUARD_CHANGE_OK=1)")
    for path, lines in sorted(changed.items()):
        if not (path.startswith("tests/") and path.endswith(".py")):
            continue
        for n, text in lines:
            if SKIP.search(text):
                out.append(f"{path}:{n}: an added skip. A test that needs node gets @pytest.mark.node "
                           "(tests/conftest.py); otherwise make it run (AGENTS.md: never weaken a check)")
    return out


def changes(base: str) -> dict[str, list[tuple[int, str]]]:
    changed = added_lines(git("diff", "--no-renames", "--no-color", "-U0", base).stdout)
    for p in git("diff", "--no-renames", "--name-only", base).stdout.split():
        changed.setdefault(p, [])
    for p in git("ls-files", "--others", "--exclude-standard").stdout.splitlines():
        try:
            changed[p] = list(enumerate((ROOT / p).read_text(encoding="utf-8").splitlines(), 1))
        except (UnicodeDecodeError, OSError):
            changed[p] = []
    return changed


def main() -> int:
    found = merge_base()
    if found is None:
        print("check_diff: no main branch shares this branch's history (set GK_BASE): skipped")
        return 0
    ref, base = found
    changed = changes(base)
    out = problems(changed, guard_change_ok=os.environ.get("GK_GUARD_CHANGE_OK") == "1")
    for p in out:
        print(p)
    print(f"check_diff: {len(out)} problem(s) in {len(changed)} changed file(s) since {ref}")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
