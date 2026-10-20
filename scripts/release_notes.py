"""Print the CHANGELOG.md section for a release, after checking the release is consistent.

    python scripts/release_notes.py 1.0.0 > notes.md

Fails (exit 1) unless app/version.py says that version and CHANGELOG.md has a "## 1.0.0" section with something in
it. The release workflow runs it on every v* tag, so a tag cannot ship an image that reports another version."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[1]):
        print("usage: release_notes.py X.Y.Z", file=sys.stderr)
        return 2
    want = argv[1]
    have = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "app/version.py").read_text(encoding="utf-8"))
    if not have or have.group(1) != want:
        print(f"app/version.py says {have.group(1) if have else '?'}, the tag says {want}", file=sys.stderr)
        return 1
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[?{re.escape(want)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m or not m.group(1).strip():
        print(f'CHANGELOG.md has no "## {want}" section (move Unreleased under it)', file=sys.stderr)
        return 1
    print(m.group(1).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
