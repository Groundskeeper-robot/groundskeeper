"""Check the tracked files for things that must never be published: private LAN addresses, personal email
addresses, anything shaped like a real Yarbo serial, databases and captures. CI runs it on every pull request.

    python scripts/check_public.py

Your own values (your robot's serial, your street, your home network) go one per line in `.private-denylist` at the
repo root. That file is git-ignored, so the check covers them without publishing them."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRIVATE_IP = re.compile(r"\b(?:192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
# user@domain.tld, not user:password@host in a URL, a package@version or an image@2x
EMAIL = re.compile(r"(?<![:/\w.+-])[A-Za-z][\w.+-]*@[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)*\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"@(?:example\.(?:com|org|net)|users\.noreply\.github\.com|anthropic\.com)$|\.(?:test|example|invalid|localhost)$|^noreply@", re.I)
SERIAL = re.compile(r"\b\d{8}[A-Z0-9]{4}\d{4}\b")          # the robot's 16-character serial
DATA_FILES = re.compile(r"\.(?:db|sqlite3?|db-wal|db-shm|pcap|pcapng)$|(?:^|/)\.env$|(?:^|/)data/", re.I)
IMAGES = re.compile(r"\.(?:png|jpe?g|webp|gif)$", re.I)
IMAGES_OK = ("brand/", "web/", "docs/screenshots/")
SKIP_TEXT = re.compile(r"\.(?:png|jpe?g|webp|gif|ico|woff2?|ttf|pdf)$|^catalog/coverage/", re.I)


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def main() -> int:
    deny = []
    f = ROOT / ".private-denylist"
    if f.exists():
        deny = [s.strip() for s in f.read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")]
    problems = []
    for path in tracked():
        if DATA_FILES.search(path):
            problems.append(f"{path}: looks like data, not code (databases, .env and data/ stay out of git)")
        if IMAGES.search(path) and not path.startswith(IMAGES_OK):
            problems.append(f"{path}: images belong in brand/, web/ or docs/screenshots/ (screenshots of the demo only)")
        if SKIP_TEXT.search(path) or path == "scripts/check_public.py":
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in PRIVATE_IP.finditer(line):
                problems.append(f"{path}:{n}: private address {m.group()} (use 192.0.2.x in examples and tests)")
            for m in EMAIL.finditer(line):
                if not EMAIL_OK.search(m.group()):
                    problems.append(f"{path}:{n}: email address (use user@example.com)")
            for m in SERIAL.finditer(line):
                problems.append(f"{path}:{n}: looks like a robot serial (use YB0000DEMO0001)")
            for d in deny:
                if d.lower() in line.lower():
                    problems.append(f"{path}:{n}: a value from .private-denylist")
    for p in problems:
        print(p)
    print(f"check_public: {len(problems)} problem(s) in {len(tracked())} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
