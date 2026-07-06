from __future__ import annotations

import asyncio

from fastapi import WebSocket

from highend_server.domain.models import TelemetryEvent


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event: TelemetryEvent) -> None:
        payload = event.model_dump(mode="json")
        async with self._lock:
            connections = list(self._connections)

        if not connections:
            return

        async def _send(websocket: WebSocket) -> WebSocket | None:
            try:
                await websocket.send_json(payload)
            except Exception:
                return websocket
            return None

        # Fan out concurrently instead of awaiting each socket sequentially, so one slow
        # client can't delay delivery to the others. `return_exceptions=True` guards against
        # any exception `_send` itself doesn't catch (e.g. cancellation) so a single bad
        # socket can never abort the gather for the rest.
        results = await asyncio.gather(
            *(_send(websocket) for websocket in connections), return_exceptions=True
        )

        stale_connections = [
            result for result in results if isinstance(result, WebSocket)
        ]

        if not stale_connections:
            return

        async with self._lock:
            for websocket in stale_connections:
                self._connections.discard(websocket)

