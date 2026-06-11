"""Connection manager for the Kramer VP-727 presentation switcher.

Maintains a persistent TCP connection to the hardware, handles keep-alives,
retries with exponential backoff, parses incoming Protocol 2000 lines, and
notifies registered status subscribers of state updates.
"""

import logging
from typing import Awaitable, Callable, List, Optional
import anyio
import anyio.abc
from config import AppConfig

logger = logging.getLogger("kramer.connection")


class ScalerConnection:
    """Manages the lifecycle of a persistent TCP connection to the scaler."""

    def __init__(self, config: AppConfig) -> None:
        """Initialize the connection manager.

        Args:
            config: Loaded configuration settings.
        """
        self.config = config
        self.status: str = "disconnected"  # "disconnected", "connecting", "connected"
        self.firmware_generation: Optional[int] = None
        self.program_source: Optional[int] = None
        self.preview_source: Optional[int] = None
        self.panel_locked: bool = True  # Default to True as per example status response

        self._stream: Optional[anyio.abc.ByteStream] = None
        self._write_lock = anyio.Lock()
        self._subscribers: List[Callable[[], Awaitable[None]]] = []
        self._tg: Optional[anyio.abc.TaskGroup] = None

    def subscribe(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register a callback to be invoked when application state changes.

        Args:
            callback: Async callback function.
        """
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Unregister a state subscriber callback.

        Args:
            callback: The callback function to remove.
        """
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify_subscribers(self) -> None:
        """Invoke all registered subscribers concurrently."""
        for callback in list(self._subscribers):
            try:
                await callback()
            except Exception as e:
                logger.error("Error invoking state subscriber callback: %s", e)

    def _set_status(self, new_status: str) -> None:
        """Update connection status and notify subscribers if changed.

        Args:
            new_status: The new status string.
        """
        if self.status != new_status:
            logger.info("Connection status changed: %s -> %s", self.status, new_status)
            self.status = new_status
            # Schedule notification in the active task group if available
            if self._tg is not None:
                self._tg.start_soon(self._notify_subscribers)

    async def run_loop(self) -> None:
        """Persistent worker loop managing TCP connection life-cycle and retries."""
        backoff = 1.0
        max_backoff = 60.0

        while True:
            self._set_status("connecting")
            try:
                logger.info(
                    "Connecting to Kramer VP-727 at %s:%d...",
                    self.config.hardware.scaler_ip,
                    self.config.hardware.scaler_port,
                )
                with anyio.fail_after(self.config.hardware.connection_timeout_seconds):
                    stream = await anyio.connect_tcp(
                        self.config.hardware.scaler_ip,
                        self.config.hardware.scaler_port,
                    )

                logger.info("Connected to Kramer VP-727.")
                self._stream = stream
                self._set_status("connected")
                backoff = 1.0  # Reset backoff on successful connection

                # Manage read loop and keepalive tasks together
                async with anyio.create_task_group() as tg:
                    self._tg = tg
                    tg.start_soon(self._read_loop, stream)
                    tg.start_soon(self._keepalive_loop)
                    tg.start_soon(self._startup_query)

            except (
                anyio.EndOfStream,
                OSError,
                TimeoutError,
                anyio.ExecutionError,
            ) as e:
                logger.warning("Connection issue: %s. Reconnecting...", e)
            except Exception as e:
                logger.exception("Unexpected error in connection loop: %s", e)
            finally:
                # Cleanup stream
                if self._stream is not None:
                    try:
                        await self._stream.aclose()
                    except Exception:
                        pass
                    self._stream = None
                self._tg = None
                self._set_status("disconnected")

            # Exponential backoff sleep
            logger.info("Waiting %.2f seconds before retry...", backoff)
            await anyio.sleep(backoff)
            backoff = min(backoff * 2.0, max_backoff)

    async def _startup_query(self) -> None:
        """Send initialization queries to sync state with the physical unit."""
        await anyio.sleep(0.5)  # Let connection stabilize briefly
        try:
            logger.info("Querying firmware version and active program bus...")
            await self.send_command("Y 0 57")
            await anyio.sleep(0.1)
            await self.send_command("Y 0 91")
        except Exception as e:
            logger.error("Startup query failed: %s", e)

    async def _read_loop(self, stream: anyio.abc.ByteStream) -> None:
        """Continuously reads bytes from stream, parsing carriage-return lines."""
        buffer = b""
        while True:
            chunk = await stream.receive(1024)
            if not chunk:
                raise anyio.EndOfStream("TCP connection closed by remote scaler.")
            buffer += chunk
            while b"\r" in buffer:
                line_bytes, buffer = buffer.split(b"\r", 1)
                line = line_bytes.decode("ascii", errors="ignore").strip()
                if line:
                    await self._parse_line(line)

    async def _keepalive_loop(self) -> None:
        """Periodically sends a non-destructive query to prevent connection timeouts."""
        interval = self.config.hardware.keepalive_interval_seconds
        while True:
            await anyio.sleep(interval)
            logger.debug("Sending keepalive query to scaler...")
            try:
                await self.send_command("Y 0 91")
            except Exception as e:
                logger.warning("Failed to send keep-alive: %s", e)
                raise  # Raise to propagate and trigger connection teardown/restart

    async def send_command(self, cmd: str) -> None:
        """Sends an ASCII string command formatted for Kramer Protocol 2000.

        Args:
            cmd: Command string (e.g. 'Y 0 91'). Appends '\r' and sends.

        Raises:
            ConnectionError: If connection is not established.
        """
        if self._stream is None:
            raise ConnectionError("No active TCP connection to Kramer VP-727 scaler.")

        formatted = f"{cmd.strip()}\r".encode("ascii")
        async with self._write_lock:
            logger.debug("Writing to socket: %r", formatted)
            await self._stream.send(formatted)

    async def _parse_line(self, line: str) -> None:
        """Parses a Protocol 2000 ASCII line response from the physical hardware."""
        logger.debug("Received line from socket: %r", line)
        parts = line.split()
        if not parts:
            return

        # Responses from hardware start with 'Z'
        if parts[0] != "Z":
            return

        if len(parts) < 3:
            return

        # parts[1] is typically Machine Number (usually '0')
        cmd = parts[2]
        state_changed = False

        if cmd == "57" and len(parts) >= 4:
            # Query firmware response: Z 0 57 <generation>
            try:
                val = int(parts[3])
                if self.firmware_generation != val:
                    self.firmware_generation = val
                    state_changed = True
            except ValueError:
                pass

        elif cmd == "91" and len(parts) >= 4:
            # Query active program response: Z 0 91 <input> -1
            try:
                val = int(parts[3])
                if self.program_source != val:
                    self.program_source = val
                    state_changed = True
            except ValueError:
                pass

        elif cmd == "1" and len(parts) >= 5:
            # Route response: Z 0 1 <input> <destination_bus>
            # Bus 1 = Program, Bus 2 = Preview
            try:
                input_ch = int(parts[3])
                bus = int(parts[4])
                if bus == 1:
                    if self.program_source != input_ch:
                        self.program_source = input_ch
                        state_changed = True
                elif bus == 2:
                    if self.preview_source != input_ch:
                        self.preview_source = input_ch
                        state_changed = True
            except ValueError:
                pass

        elif cmd == "16" and len(parts) >= 5:
            # Take command execution acknowledgment: Z 0 16 3 1
            # A TAKE swaps the staged preview onto program.
            # To ensure full consistency, update state and query program source.
            if self.program_source is not None and self.preview_source is not None:
                # Perform immediate local swap
                self.program_source, self.preview_source = (
                    self.preview_source,
                    self.program_source,
                )
                state_changed = True

            # Fire off a query to verify program source
            if self._tg is not None:
                self._tg.start_soon(self.send_command, "Y 0 91")

        elif cmd == "161":
            # Custom resolution timings written: Z 0 161 1
            logger.info(
                "Custom resolution timings written and synchronized successfully."
            )

        if state_changed:
            await self._notify_subscribers()
