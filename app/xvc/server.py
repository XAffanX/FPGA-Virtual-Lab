"""Xilinx Virtual Cable (XVC 1.0) TCP server exposing the emulated XC7A35T JTAG port.

Protocol (Xilinx XAPP1251 / xvcServer reference):
  "getinfo:"                                 -> "xvcServer_v1.0:<max vector bytes>\\n"
  "settck:" <u32 period_ns LE>               -> <u32 period_ns LE>
  "shift:" <u32 nbits LE> <tms bytes> <tdi bytes>  -> <tdo bytes>     (bytes = ceil(nbits/8))

Vivado connects with:  open_hw_target -xvc_url localhost:2542
(Hardware Manager > Open Target > Open New Target > Add Xilinx Virtual Cable).
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from .jtag import Artix7Jtag

MAX_VECTOR_BYTES = 32768
DEFAULT_PORT = 2542


class XvcServer:
    def __init__(self, device: Artix7Jtag, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 on_event=lambda kind, msg: None):
        self.device = device
        self.host, self.port = host, port
        self.on_event = on_event
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.clients = 0
        self.bytes_in = 0
        self.last_activity = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, "SO_EXCLUSIVEADDRUSE")
                     else socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))       # raises OSError if the port is taken
        s.listen(2)
        s.settimeout(0.5)
        self._sock = s
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="xvc-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self._sock = None
        self._thread = None

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn, addr), daemon=True, name="xvc-client").start()

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("client closed the connection")
            buf += chunk
        return bytes(buf)

    def _client(self, conn: socket.socket, addr) -> None:
        self.clients += 1
        self.on_event("connected", f"Vivado hw_server connected from {addr[0]}:{addr[1]}")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(None)
        try:
            while not self._stop.is_set():
                head = self._recv_exact(conn, 2)
                if head == b"ge":
                    self._recv_exact(conn, 6)                      # "tinfo:"
                    conn.sendall(f"xvcServer_v1.0:{MAX_VECTOR_BYTES}\n".encode())
                elif head == b"se":
                    self._recv_exact(conn, 5)                      # "ttck:"
                    period = self._recv_exact(conn, 4)
                    conn.sendall(period)
                elif head == b"sh":
                    self._recv_exact(conn, 4)                      # "ift:"
                    nbits = struct.unpack("<I", self._recv_exact(conn, 4))[0]
                    nbytes = (nbits + 7) // 8
                    if nbytes > MAX_VECTOR_BYTES:
                        raise ConnectionError(f"shift of {nbits} bits exceeds the advertised maximum")
                    tms = self._recv_exact(conn, nbytes)
                    tdi = self._recv_exact(conn, nbytes)
                    self.bytes_in += 2 * nbytes
                    self.last_activity = time.monotonic()
                    conn.sendall(self.device.shift(nbits, tms, tdi))
                else:
                    raise ConnectionError(f"invalid XVC command {head!r}")
        except (ConnectionError, OSError) as exc:
            if not self._stop.is_set():
                self.on_event("disconnected", f"Vivado hw_server disconnected ({exc})")
        finally:
            self.clients -= 1
            try:
                conn.close()
            except OSError:
                pass
