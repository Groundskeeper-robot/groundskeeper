"""Frigate (NVR) client: yard-camera snapshots for the follow-cam, and recent detections for the yard-clear gate.

The Frigate gate is in addition to the robot's own person detection, never a replacement for it (spec §11).
"""
import logging
import time

import httpx

log = logging.getLogger(__name__)
DEFAULT_LABELS = ("person", "dog", "cat")
MAX_STREAMS = 6


class Frigate:
    def __init__(self, url: str | None):
        self.http = None
        self._set(url)

    def _set(self, url: str | None) -> None:
        self.url = (url or "").rstrip("/")
        self.enabled = bool(self.url)
        self.http = httpx.AsyncClient(timeout=10) if self.enabled else None
        self.error = "" if self.enabled else "not configured (Settings → Cameras)"
        self._cameras: list[str] = []
        self._cameras_at = 0.0
        self._streams = 0

    async def configure(self, url: str | None) -> None:
        """Point at another Frigate (or none). The camera list is fetched again on the next call."""
        await self.close()
        self._set(url)
        if self.enabled:
            await self.cameras()

    async def close(self) -> None:
        if self.http:
            await self.http.aclose()
            self.http = None

    async def cameras(self) -> list[str]:
        if not self.enabled:
            return []
        if time.time() - self._cameras_at < 600 and self._cameras:
            return self._cameras
        try:
            r = await self.http.get(f"{self.url}/api/config")
            r.raise_for_status()
            self._cameras = sorted((r.json().get("cameras") or {}).keys())
            self._cameras_at = time.time()
            self.error = ""
        except Exception as exc:
            self.error = f"config: {exc}"
        return self._cameras

    async def snapshot(self, camera: str, height: int = 360) -> bytes | None:
        if not self.enabled or camera not in await self.cameras():
            return None
        try:
            r = await self.http.get(f"{self.url}/api/{camera}/latest.jpg", params={"height": height})
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return r.content
        except Exception as exc:
            self.error = f"snapshot: {exc}"
        return None

    async def stream(self, camera: str, height: int = 720, fps: int = 5):
        """Frigate's live MJPEG of a camera (multipart JPEG frames, under a second behind; the frame rate is capped by
        the camera's detect fps). Returns (content type, async byte iterator) or None. Closing the iterator closes
        the upstream request. At most MAX_STREAMS at once, so a pile of forgotten tabs cannot swamp Frigate."""
        if not self.enabled or self._streams >= MAX_STREAMS or camera not in await self.cameras():
            return None
        req = self.http.build_request("GET", f"{self.url}/api/{camera}", params={"fps": fps, "height": height},
                                      timeout=httpx.Timeout(10, read=30))
        try:
            r = await self.http.send(req, stream=True)
        except Exception as exc:
            self.error = f"stream: {exc}"
            return None
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("multipart/"):
            await r.aclose()
            return None
        self._streams += 1

        async def chunks():
            try:
                async for b in r.aiter_raw():
                    yield b
            except httpx.HTTPError:
                pass  # Frigate went away mid-stream; the browser falls back to snapshots
            finally:
                self._streams = max(0, self._streams - 1)  # configure() may have reset the count meanwhile
                await r.aclose()
        return ctype, chunks()

    async def detections(self, cameras: list[str], labels=DEFAULT_LABELS, since_s: float = 600) -> dict:
        """Objects of interest on the yard cameras now or in the last since_s seconds.
        Returns {"ok": bool, "events": [...]}; ok=False means Frigate could not be asked (treat as unknown)."""
        if not self.enabled or not cameras:
            return {"ok": False, "events": [], "reason": self.error or "no yard cameras selected"}
        params = {"cameras": ",".join(cameras), "labels": ",".join(labels), "limit": 25,
                  "after": time.time() - since_s}
        try:
            recent = await self.http.get(f"{self.url}/api/events", params=params)
            live = await self.http.get(f"{self.url}/api/events", params={**params, "in_progress": 1, "after": 0})
            recent.raise_for_status()
            live.raise_for_status()
            seen, out = set(), []
            for e in live.json() + recent.json():
                if e.get("id") in seen:
                    continue
                seen.add(e.get("id"))
                out.append({"id": e.get("id"), "camera": e.get("camera"), "label": e.get("label"),
                            "start": e.get("start_time"), "end": e.get("end_time"),
                            "in_progress": e.get("end_time") is None})
            self.error = ""
            return {"ok": True, "events": out}
        except Exception as exc:
            self.error = f"events: {exc}"
            return {"ok": False, "events": [], "reason": self.error}

    def status(self) -> dict:
        return {"enabled": self.enabled, "url": self.url or None, "error": self.error, "cameras": len(self._cameras)}
