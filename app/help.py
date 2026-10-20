"""In-panel help: the user docs in docs/user/*.md and the changelog, served as markdown for the Help view
(web/help.js renders it). Each page starts with a `# Title` line; an optional `<!-- order: N -->` comment on the
second line sorts the index. Pages are read from disk on each request, so editing one needs no restart."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "docs" / "user"
CHANGELOG = ROOT / "CHANGELOG.md"
# Pages kept elsewhere in the repo, shown in Help too: id -> (file, order in the index).
EXTRA = {"imagery-finding": (ROOT / "catalog" / "FINDING.md", 86), "changelog": (CHANGELOG, 1000)}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_ORDER = re.compile(r"<!--\s*order:\s*(\d+)\s*-->")


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _path(page_id: str) -> Path | None:
    if page_id in EXTRA:
        p = EXTRA[page_id][0]
        return p if p.is_file() else None
    if not _ID.match(page_id):
        return None
    p = PAGES / f"{page_id}.md"
    return p if p.is_file() else None


def pages() -> list[dict]:
    out = []
    for p in sorted(PAGES.glob("*.md")) if PAGES.is_dir() else []:
        if not _ID.match(p.stem):
            continue
        text = p.read_text(encoding="utf-8")
        m = _ORDER.search(text[:400])
        out.append({"id": p.stem, "title": _title(text, p.stem), "order": int(m.group(1)) if m else 999})
    for pid, (p, order) in EXTRA.items():
        if p.is_file():
            out.append({"id": pid, "title": _title(p.read_text(encoding="utf-8"), pid), "order": order})
    return sorted(out, key=lambda x: (x["order"], x["title"]))


def page(page_id: str) -> dict | None:
    p = _path(page_id)
    if p is None:
        return None
    text = p.read_text(encoding="utf-8")
    return {"id": page_id, "title": _title(text, page_id), "markdown": text}
