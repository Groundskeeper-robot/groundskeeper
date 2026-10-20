"""Run every check a pull request must pass, in the order CI runs them, and say which failed. One command for people
and AI agents alike: when this prints "all checks passed", the change is ready for review.

    python scripts/check.py            # everything
    python scripts/check.py --quick    # skip the full test suite (the guard tests still run)

Node.js is optional: without it the front-end syntax check is skipped with a note, and CI still runs it."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def steps(quick: bool) -> list[tuple[str, list[list[str]]]]:
    out = [
        ("guard tests (rules in AGENTS.md)", [[PY, "-m", "pytest", "-q", "tests/test_guards.py"]]),
        ("imagery catalog", [[PY, "-m", "app.imagery", "verify"],
                             [PY, "-m", "app.imagery", "schema", "--check"],
                             [PY, "-m", "app.imagery", "table", "--check"]]),
        ("nothing private in tracked files", [[PY, "scripts/check_public.py"]]),
    ]
    if not quick:
        out.insert(1, ("all tests", [[PY, "-m", "pytest", "-q"]]))
    node = shutil.which("node")
    if node:
        js = sorted((ROOT / "web").glob("*.js"))
        out.append(("front-end scripts parse", [[node, "--check", str(f)] for f in js]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="skip the full test suite")
    args = ap.parse_args()
    failed = []
    for name, cmds in steps(args.quick):
        print(f"\n== {name}", flush=True)
        for cmd in cmds:
            if subprocess.run(cmd, cwd=ROOT).returncode != 0:
                failed.append(name)
                break
    if not shutil.which("node"):
        print("\n(note) node not found: front-end syntax check skipped; CI runs it")
    if failed:
        print("\nFAILED: " + ", ".join(failed))
        return 1
    print("\nall checks passed" + (" (quick: full test suite skipped)" if args.quick else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
