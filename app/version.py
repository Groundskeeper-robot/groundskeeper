"""The panel's version. Bump it with each release that changes behaviour, move CHANGELOG.md's "Unreleased" entry
under the new number in the same commit, and tag the commit `v<version>`. The build (git commit) comes from the
image: scripts/deploy.sh passes it as PANEL_BUILD."""
import os
import re

__version__ = "0.16.0"
_build = (os.environ.get("PANEL_BUILD") or "").strip().lower()
BUILD = (_build[:7] if re.fullmatch(r"[0-9a-f]{8,40}", _build) else _build[:20]) or None   # a full commit id is cut to 7


def version_info() -> dict:
    return {"version": __version__, "build": BUILD}
