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

        stale_connections: list[WebSocket] = []
        for websocket in connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)

        if not stale_connections:
            return

        async with self._lock:
            for websocket in stale_connections:
                self._connections.discard(websocket)

