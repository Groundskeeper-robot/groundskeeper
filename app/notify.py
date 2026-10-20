"""Notifications: Home Assistant (a notify service, e.g. notify.mobile_app_<phone>) and/or a plain JSON webhook.
Every notification is also written to the events log, so nothing depends on a channel being configured."""
import logging

import httpx

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, store, ha_url: str | None, ha_token: str | None):
        self.store = store
        self.http = httpx.AsyncClient(timeout=10)
        self.last_error = ""
        self.configure(ha_url, ha_token)

    def configure(self, ha_url: str | None, ha_token: str | None) -> None:
        self.ha_url = (ha_url or "").rstrip("/")
        self.ha_token = ha_token or ""

    async def close(self) -> None:
        await self.http.aclose()

    def channels(self) -> dict:
        service = self.store.setting("ha_notify_service")
        webhook = self.store.setting("webhook_url")
        return {"ha": bool(self.ha_url and self.ha_token and service), "ha_service": service, "webhook": bool(webhook)}

    async def send(self, kind: str, title: str, message: str, url: str | None = None, image: str | None = None) -> dict:
        self.store.event(kind, f"{title}: {message}")
        sent = {}
        service = self.store.setting("ha_notify_service")
        if self.ha_url and self.ha_token and service:
            domain, _, name = service.partition(".")
            data = {"title": title, "message": message, "data": {"group": "yarbo", "tag": kind}}
            if url:
                data["data"]["url"] = url
                data["data"]["clickAction"] = url
            if image:
                data["data"]["image"] = image
            try:
                r = await self.http.post(f"{self.ha_url}/api/services/{domain}/{name}", json=data,
                                         headers={"Authorization": f"Bearer {self.ha_token}"})
                r.raise_for_status()
                sent["ha"] = True
            except Exception as exc:
                sent["ha"] = False
                self.last_error = f"HA notify: {exc}"
                log.warning(self.last_error)
        webhook = self.store.setting("webhook_url")
        if webhook:
            try:
                r = await self.http.post(webhook, json={"kind": kind, "title": title, "message": message,
                                                        "url": url, "image": image})
                r.raise_for_status()
                sent["webhook"] = True
            except Exception as exc:
                sent["webhook"] = False
                self.last_error = f"webhook: {exc}"
                log.warning(self.last_error)
        return sent
