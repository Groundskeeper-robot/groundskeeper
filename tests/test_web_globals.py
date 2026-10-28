"""The page loads its scripts as classic scripts (web/index.html), which share one global scope: a top-level name
declared twice with const/let/class stops the second file from loading at all. `node --check` sees each file alone,
so it can't catch this."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TOP = re.compile(r"^(?:const|let|class|function|async function)\s+([A-Za-z_$][\w$]*)", re.M)


def test_no_top_level_name_is_declared_in_two_page_scripts():
    pages = re.findall(r'<script src="/([\w.]+\.js)"', (WEB / "index.html").read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    clashes = []
    for name in pages:
        p = WEB / name
        if not p.exists():                       # vendored files are not in the repo
            continue
        for ident in TOP.findall(p.read_text(encoding="utf-8")):
            if ident in seen and seen[ident] != name:
                clashes.append(f"{ident}: {seen[ident]} and {name}")
            seen.setdefault(ident, name)
    assert not clashes, f"declared at the top level of two scripts: {clashes}"
