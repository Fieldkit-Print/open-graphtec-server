from __future__ import annotations

import socket
import time
from typing import Optional


ESC = "\x1b"
STX = "\x02"
ETX = "\x03"
RS = "\x1e"


# Source status values allowed for each destination status.
ALLOWED_SOURCE_STATUSES: dict[int, set[int]] = {
    0: {1, 5, 6, 7, -999},
    1: {0, 5, 6, -999},
    2: {1, -999},
    3: {2, -999},
    4: {2, 3, 4, -999},
    5: {4, 6, -999},
    6: {1, 5, -999},
    7: {1, 2, 3, 4, 5, 6, -999},
}


def is_allowed_transition(src_status: int, dst_status: int) -> bool:
    if dst_status == -999:
        return True
    if dst_status not in ALLOWED_SOURCE_STATUSES:
        # Future status values must be ignored.
        return True
    return src_status in ALLOWED_SOURCE_STATUSES[dst_status]


def clean_response(response: str) -> str:
    return (
        response.replace(ETX, "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )


def parse_int_response(response: str) -> Optional[int]:
    cleaned = clean_response(response)
    if cleaned == "":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


class DataLinkClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        timeout_seconds: float,
        retry_total_ms: int,
        retry_interval_ms: int,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.retry_total_ms = retry_total_ms
        self.retry_interval_ms = retry_interval_ms

    def get_data_link_status(self) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d1:", expect_response=True
        )
        value = parse_int_response(response)
        if value is None:
            raise RuntimeError(f"Could not parse DLS status from {response!r}")
        return value

    def get_barcode_link_info(self) -> str:
        response = self._send_text_command_with_retry(
            f"{ESC}.d2:", expect_response=True
        )
        return clean_response(response)

    def send_job_list(self, names: list[str]) -> int:
        if len(names) > 8:
            raise ValueError("Data Link supports up to 8 jobs in one list.")

        if names:
            payload = STX + RS.join(names) + ETX
        else:
            payload = ETX
        response = self._send_text_command_with_retry(
            f"{ESC}.d3{payload}", expect_response=True
        )
        value = parse_int_response(response)
        if value is None:
            raise RuntimeError(
                f"Could not parse job-list response from {response!r}"
            )
        return value

    def get_selected_job_index(self) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d4:", expect_response=True
        )
        value = parse_int_response(response)
        # Empty string means no selected job.
        if value is None:
            return -1
        return value

    def set_regmark_position(
        self, fx: int, fy: int, rx: int, ry: int
    ) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d5;0;{fx};{fy};{rx};{ry}:",
            expect_response=True,
        )
        value = parse_int_response(response)
        if value is None:
            raise RuntimeError(
                f"Could not parse regmark response from {response!r}"
            )
        return value

    def set_command_type(self, command_type: int) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d6;{command_type}:",
            expect_response=True,
        )
        value = parse_int_response(response)
        if value is None:
            raise RuntimeError(
                f"Could not parse command-type response from {response!r}"
            )
        return value

    def send_command_sequence(self, command_sequence: bytes) -> None:
        self._send_bytes_with_retry(command_sequence, expect_response=False)

    def _send_text_command_with_retry(
        self, command: str, *, expect_response: bool
    ) -> str:
        payload = command.encode("utf-8", errors="ignore")
        return self._send_bytes_with_retry(
            payload, expect_response=expect_response
        )

    def _send_bytes_with_retry(
        self, payload: bytes, *, expect_response: bool
    ) -> str:
        last_error: Optional[Exception] = None
        deadline = time.monotonic() + (self.retry_total_ms / 1000.0)

        while time.monotonic() < deadline:
            try:
                return self._send_once(payload, expect_response=expect_response)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(self.retry_interval_ms / 1000.0)

        if last_error is None:
            raise RuntimeError("Failed to send command for unknown reason.")
        raise last_error

    def _send_once(self, payload: bytes, *, expect_response: bool) -> str:
        with socket.create_connection(
            (self.host, self.port), timeout=self.timeout_seconds
        ) as sock:
            sock.settimeout(self.timeout_seconds)
            sock.sendall(payload)
            if not expect_response:
                return ""

            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\x03" in chunk:
                    break
            return b"".join(chunks).decode("utf-8", errors="ignore")

