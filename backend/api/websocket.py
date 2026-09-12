"""WebSocket fan-out.

The endpoint here never calls IBM. It reads the cache the poller fills and
pushes it to whoever is connected -- which is exactly why opening twenty tabs
costs IBM nothing.

Broadcast is *per-client isolated*: one wedged or half-closed socket cannot
stall delivery to the others, and dead clients are reaped rather than retried.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..models import TelemetrySnapshot

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """Tracks live WebSocket clients and pushes snapshots to them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("WebSocket client connected (%d total).", self.client_count)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining).", self.client_count)

    async def send_snapshot(self, websocket: WebSocket, snapshot: TelemetrySnapshot) -> None:
        await websocket.send_text(snapshot.model_dump_json())

    async def broadcast(self, snapshot: TelemetrySnapshot) -> None:
        """Push one snapshot to every client, dropping any that fail."""
        async with self._lock:
            targets = list(self._connections)
        if not targets:
            return

        payload = snapshot.model_dump_json()
        results = await asyncio.gather(
            *(client.send_text(payload) for client in targets),
            return_exceptions=True,
        )

        stale = [
            client
            for client, outcome in zip(targets, results)
            if isinstance(outcome, BaseException)
        ]
        if stale:
            async with self._lock:
                for client in stale:
                    self._connections.discard(client)
            logger.info("Dropped %d unresponsive WebSocket client(s).", len(stale))


@router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    """Stream telemetry snapshots to a connected browser."""
    manager: ConnectionManager = websocket.app.state.connections
    store = websocket.app.state.store

    await manager.connect(websocket)
    try:
        # Paint immediately from cache rather than making the client wait up to
        # a full poll interval for the first frame.
        current = store.get_snapshot()
        if current is not None:
            await manager.send_snapshot(websocket, current)

        # The client has nothing it needs to say; this loop exists purely to
        # notice the socket closing. A stray "ping" gets a "pong" so browsers
        # behind proxies can keep the connection warm.
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket closed with an unexpected error.")
    finally:
        await manager.disconnect(websocket)
