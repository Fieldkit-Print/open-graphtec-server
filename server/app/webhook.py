"""Outbound webhook notifications.

Fires a JSON POST to the configured URL for cut-workflow events:

- ``job.sent``          a cutter scanned a barcode and received a job
- ``barcode.no_match``  a cutter scanned a barcode with no matching jobs

Delivery is fire-and-forget on a daemon thread with retries, so the Data
Link poll loop is never blocked by a slow receiver. When a secret is
configured, the body is signed with HMAC-SHA256 in the
``X-OGS-Signature`` header (``sha256=<hexdigest>``).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(
        self,
        url: str,
        *,
        secret: str = "",
        timeout_seconds: float = 5.0,
        attempts: int = 3,
    ) -> None:
        self.url = url.strip()
        self.secret = secret
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._lock = threading.Lock()
        self._delivered = 0
        self._failed = 0
        self._last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "configured": self.enabled,
                "delivered": self._delivered,
                "failed": self._failed,
                "last_error": self._last_error,
            }

    def notify(self, event: str, **payload: object) -> None:
        if not self.enabled:
            return
        body = json.dumps(
            {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **payload,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        threading.Thread(
            target=self._deliver, args=(event, body), daemon=True,
            name="webhook-delivery",
        ).start()

    def _deliver(self, event: str, body: bytes) -> None:
        headers = {"Content-Type": "application/json",
                   "User-Agent": "open-graphtec-server"}
        if self.secret:
            digest = hmac.new(
                self.secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers["X-OGS-Signature"] = f"sha256={digest}"

        last_error: str | None = None
        for attempt in range(self.attempts):
            try:
                request = urllib.request.Request(
                    self.url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    if 200 <= response.status < 300:
                        with self._lock:
                            self._delivered += 1
                        return
                    last_error = f"receiver returned HTTP {response.status}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            if attempt + 1 < self.attempts:
                time.sleep(1.0 * (attempt + 1))
        with self._lock:
            self._failed += 1
            self._last_error = f"{event}: {last_error}"
        logger.warning("Webhook delivery failed (%s): %s", event, last_error)
