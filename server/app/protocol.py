from __future__ import annotations

import socket
import time
from typing import Optional


ESC = "\x1b"
STX = "\x02"
ETX = "\x03"
RS = "\x1e"

MAX_JOBS_PER_LIST = 8
MAX_JOB_NAME_LENGTH = 25

# ESC.C31;16: reply codes -> step size in mm (CE8000/FC9000 spec 3.3.14).
STEP_SIZE_CODE_TO_MM = {1: 0.100, 2: 0.050, 3: 0.025, 4: 0.010}

COMMAND_SETTING_LABELS = {0: "GP-GL", 1: "HP-GL", 2: "AUTO"}

# Human labels for the ESC.d1 Data Link status values (guideline Table 1).
DLS_STATUS_LABELS = {
    -999: "Not communicating",
    0: "Stopped",
    1: "Scanning barcode",
    2: "Requesting job list",
    3: "Selecting job",
    4: "Job determined",
    5: "Cutting",
    6: "User operating",
    7: "Error",
}


class SendError(RuntimeError):
    """Failed before the payload was (fully) delivered. Safe to retry."""


class ResponseError(RuntimeError):
    """Payload was delivered but no valid response arrived.

    Not blindly retried: re-sending would repeat non-idempotent commands
    (ESC.d5/d6 are once-per-job) after the cutter may already have acted.
    """


# Source status values allowed for each destination status.
# Sources {2, 3} for destination 0 cover the Rev 1.02 ESC-key cancel paths.
ALLOWED_SOURCE_STATUSES: dict[int, set[int]] = {
    0: {1, 2, 3, 5, 6, 7, -999},
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


def validate_job_name(name: str) -> str:
    """Enforce the spec's job-name rules: 1-25 printable ASCII characters.

    Control characters would corrupt the STX/RS/ETX framing of ESC.d3 and
    shift the cutter's selection index onto the wrong job.
    """
    if not name:
        raise ValueError("Job name must not be empty.")
    if len(name) > MAX_JOB_NAME_LENGTH:
        raise ValueError(
            f"Job name {name!r} exceeds {MAX_JOB_NAME_LENGTH} characters."
        )
    if not all(0x20 <= ord(ch) <= 0x7E for ch in name):
        raise ValueError(
            f"Job name {name!r} must contain only printable ASCII characters."
        )
    return name


def sanitize_job_name(name: str, *, fallback: str = "job") -> str:
    """Coerce an arbitrary string into a valid job name (best effort)."""
    cleaned = "".join(
        ch for ch in name if 0x20 <= ord(ch) <= 0x7E
    ).strip()[:MAX_JOB_NAME_LENGTH]
    if not cleaned:
        cleaned = fallback[:MAX_JOB_NAME_LENGTH] or "job"
    return cleaned


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

    def get_model_info(self) -> str:
        """Read model name and firmware version (e.g. 'FC9000-140, V1.39')."""
        response = self._send_text_command_with_retry(
            f"{ESC}.C31;9;1:", expect_response=True
        )
        return clean_response(response)

    def get_step_size_code(self) -> Optional[int]:
        """Read the GP-GL STEP SIZE menu setting (see STEP_SIZE_CODE_TO_MM)."""
        response = self._send_text_command_with_retry(
            f"{ESC}.C31;16:", expect_response=True
        )
        return parse_int_response(response)

    def get_command_setting_code(self) -> Optional[int]:
        """Read the COMMAND menu setting: 0 GP-GL, 1 HP-GL, 2 AUTO."""
        response = self._send_text_command_with_retry(
            f"{ESC}.C31;24:", expect_response=True
        )
        return parse_int_response(response)

    def get_data_link_status(self) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d1:", expect_response=True
        )
        value = parse_int_response(response)
        if value is None:
            raise ResponseError(f"Could not parse DLS status from {response!r}")
        return value

    def get_barcode_link_info(self) -> str:
        response = self._send_text_command_with_retry(
            f"{ESC}.d2:", expect_response=True
        )
        return clean_response(response)

    def send_job_list(self, names: list[str]) -> int:
        if len(names) > MAX_JOBS_PER_LIST:
            raise ValueError(
                f"Data Link supports up to {MAX_JOBS_PER_LIST} jobs in one list."
            )
        for name in names:
            validate_job_name(name)

        if names:
            payload = STX + RS.join(names) + ETX
        else:
            payload = ETX
        response = self._send_text_command_with_retry(
            f"{ESC}.d3{payload}", expect_response=True
        )
        value = parse_int_response(response)
        if value is None:
            raise ResponseError(
                f"Could not parse job-list response from {response!r}"
            )
        return value

    def get_selected_job_index(self) -> Optional[int]:
        """Return the selected index, or None when the cutter sends an
        empty response (the user picked a job this server did not send)."""
        response = self._send_text_command_with_retry(
            f"{ESC}.d4:", expect_response=True
        )
        return parse_int_response(response)

    def set_regmark_position(
        self, fx: int, fy: int, rx: int, ry: int
    ) -> int:
        response = self._send_text_command_with_retry(
            f"{ESC}.d5;0;{fx};{fy};{rx};{ry}:",
            expect_response=True,
        )
        value = parse_int_response(response)
        if value is None:
            raise ResponseError(
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
            raise ResponseError(
                f"Could not parse command-type response from {response!r}"
            )
        return value

    def send_command_sequence(self, command_sequence: bytes) -> None:
        self._send_bytes_with_retry(command_sequence, expect_response=False)

    def _send_text_command_with_retry(
        self, command: str, *, expect_response: bool
    ) -> str:
        payload = command.encode("ascii", errors="ignore")
        return self._send_bytes_with_retry(
            payload, expect_response=expect_response
        )

    def _send_bytes_with_retry(
        self, payload: bytes, *, expect_response: bool
    ) -> str:
        """Send with the spec's retry loop (guideline 3-5 / Appendix B).

        Only send-phase failures are retried: the retry loop exists so
        multiple tools can time-share the cutter's single LAN connection,
        and the budget is measured as CUMULATIVE WAIT time between
        attempts — a slow failed attempt must not consume the budget.
        Response-phase failures are raised immediately (see ResponseError).
        """
        waited_ms = 0
        while True:
            try:
                return self._send_once(payload, expect_response=expect_response)
            except ResponseError:
                raise
            except Exception as exc:  # noqa: BLE001
                if waited_ms >= self.retry_total_ms:
                    raise SendError(
                        f"Send failed after {waited_ms} ms of retries: {exc}"
                    ) from exc
                time.sleep(self.retry_interval_ms / 1000.0)
                waited_ms += self.retry_interval_ms

    def _send_once(self, payload: bytes, *, expect_response: bool) -> str:
        sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout_seconds
        )
        try:
            sock.settimeout(self.timeout_seconds)
            sock.sendall(payload)

            if not expect_response:
                return ""

            try:
                chunks: list[bytes] = []
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\x03" in chunk:
                        break
                raw = b"".join(chunks)
            except Exception as exc:  # noqa: BLE001
                raise ResponseError(
                    f"Failed while reading response: {exc}"
                ) from exc

            # A response without the terminating ETX means the connection
            # dropped mid-reply. Treating it as valid would make a truncated
            # ESC.d4 reply indistinguishable from "no job selected".
            if b"\x03" not in raw:
                raise ResponseError(
                    f"Response was truncated (no ETX terminator): {raw!r}"
                )
            return raw.decode("ascii", errors="ignore")
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
