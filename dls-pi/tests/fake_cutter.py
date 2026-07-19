"""A scriptable fake Graphtec cutter for protocol/worker tests.

Speaks the cutter side of the Data Link protocol (ESC.d1-d6) over TCP.
Like the real hardware it accepts one connection at a time and expects a
fresh connection per command. Fault injection lets tests exercise
truncated responses, missing ETX terminators, and dead ports.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional


ESC = b"\x1b"
ETX = b"\x03"
STX = b"\x02"
RS = b"\x1e"


class FakeCutter:
    def __init__(self) -> None:
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(0.1)
        self.port: int = self._server.getsockname()[1]
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cond = threading.Condition()

        # Scriptable state (read on each request).
        self.status: int = 0
        self.model_info: str = "FAKE-9000, V1.00"
        self.step_size_code: int = 1  # 1=0.1mm 2=0.05 3=0.025 4=0.01
        self.barcode: str = "A12345678"
        self.selected_reply: str = "0"     # reply body for ESC.d4
        self.job_list_reply: str = "0"     # reply body for ESC.d3
        self.regmark_reply: str = "0"      # reply body for ESC.d5
        self.command_type_reply: str = "0" # reply body for ESC.d6

        # Fault injection.
        self.omit_etx: bool = False        # respond without the terminator
        self.close_without_reply: bool = False

        # Recorded traffic.
        self.d1_count = 0
        self.received_job_lists: list[list[str]] = []
        self.received_regmarks: list[bytes] = []
        self.received_command_types: list[bytes] = []
        self.received_sequences: list[bytes] = []
        self.connection_count = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "FakeCutter":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._server.close()

    def __enter__(self) -> "FakeCutter":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- test helpers ------------------------------------------------------

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        with self._cond:
            return self._cond.wait_for(predicate, timeout=timeout)

    def _notify(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # -- server loop -------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(1.0)
                self.connection_count += 1
                try:
                    self._handle(conn)
                except Exception:  # noqa: BLE001 - keep serving
                    pass
            self._notify()

    def _read_message(self, conn: socket.socket) -> bytes:
        """Read one inbound message.

        ESC.d3 payloads end with ETX; other ESC.d commands end with ':';
        anything else is a raw command sequence, read until EOF.
        """
        buffer = b""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if buffer.startswith(ESC + b".d3"):
                if buffer.endswith(ETX):
                    return buffer
            elif buffer.startswith((ESC + b".d", ESC + b".C")):
                if buffer.endswith(b":"):
                    return buffer
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return buffer
            buffer += chunk
        return buffer

    def _reply(self, conn: socket.socket, body: str) -> None:
        if self.close_without_reply:
            return
        payload = body.encode("ascii")
        if not self.omit_etx:
            payload += ETX
        conn.sendall(payload)

    def _handle(self, conn: socket.socket) -> None:
        message = self._read_message(conn)
        if not message:
            return

        if message.startswith(ESC + b".C31;9"):
            self._reply(conn, self.model_info)
        elif message.startswith(ESC + b".C31;16"):
            # Real FC9000 firmware pads numeric ESC.C replies to 5 bytes.
            self._reply(conn, f"{self.step_size_code:>5}")
        elif message.startswith(ESC + b".d1"):
            self.d1_count += 1
            self._reply(conn, str(self.status))
        elif message.startswith(ESC + b".d2"):
            self._reply(conn, self.barcode)
        elif message.startswith(ESC + b".d3"):
            payload = message[len(ESC + b".d3"):]
            names: list[str] = []
            if payload.startswith(STX):
                body = payload[1:].rstrip(ETX.decode().encode())
                names = [
                    part.decode("ascii") for part in body.split(RS) if part
                ]
            self.received_job_lists.append(names)
            self._reply(conn, self.job_list_reply)
        elif message.startswith(ESC + b".d4"):
            self._reply(conn, self.selected_reply)
        elif message.startswith(ESC + b".d5"):
            self.received_regmarks.append(message)
            self._reply(conn, self.regmark_reply)
        elif message.startswith(ESC + b".d6"):
            self.received_command_types.append(message)
            self._reply(conn, self.command_type_reply)
        else:
            # Raw command sequence: no response is ever sent.
            self.received_sequences.append(message)
