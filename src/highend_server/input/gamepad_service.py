from __future__ import annotations

import asyncio
import logging
import select
import threading
import time
from collections.abc import Awaitable, Callable
from time import monotonic

from highend_server.config import Settings
from highend_server.domain.models import (
    GamepadSource,
    GamepadState,
    TelemetryEvent,
    WebGamepadUpdate,
)

EventSink = Callable[[TelemetryEvent], Awaitable[None]]

logger = logging.getLogger(__name__)

WEB_AXIS_NAMES = ("left_x", "left_y", "right_x", "right_y")
WEB_BUTTON_NAMES = (
    "a",
    "b",
    "x",
    "y",
    "lb",
    "rb",
    "left_trigger",
    "right_trigger",
    "back",
    "start",
    "left_stick",
    "right_stick",
    "dpad_up",
    "dpad_down",
    "dpad_left",
    "dpad_right",
)


def normalize_axis(value: int, minimum: int, maximum: int, *, trigger: bool = False) -> float:
    if maximum <= minimum:
        return 0.0
    ratio = (value - minimum) / (maximum - minimum)
    normalized = ratio if trigger else ratio * 2.0 - 1.0
    return max(0.0 if trigger else -1.0, min(1.0, normalized))


class GamepadService:
    """Merge local evdev and browser Gamepad API input for display/logging only."""

    def __init__(self, *, settings: Settings, event_sink: EventSink) -> None:
        self.settings = settings
        self._event_sink = event_sink
        self._lock = threading.Lock()
        self._state = GamepadState()
        self._local_state: GamepadState | None = None
        self._local_heartbeat = 0.0
        self._web_state: GamepadState | None = None
        self._web_heartbeat = 0.0
        self._stop_event = threading.Event()
        self._local_thread: threading.Thread | None = None
        self._publish_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> GamepadState:
        with self._lock:
            return self._state.model_copy(deep=True)

    async def start(self) -> None:
        self._stop_event.clear()
        if self.settings.gamepad_local_enabled:
            self._local_thread = threading.Thread(
                target=self._local_reader_loop,
                name="gamepad-evdev",
                daemon=True,
            )
            self._local_thread.start()
        self._publish_task = asyncio.create_task(self._publish_loop(), name="gamepad-publish")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._publish_task is not None:
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass
            self._publish_task = None
        if self._local_thread is not None:
            await asyncio.to_thread(self._local_thread.join, 1.0)
            self._local_thread = None

    async def update_web(self, update: WebGamepadUpdate) -> None:
        if not self.settings.gamepad_web_enabled:
            return
        now = monotonic()
        if not update.connected:
            with self._lock:
                self._web_state = None
                self._web_heartbeat = 0.0
            return

        raw_axes = {f"axis_{index}": float(value) for index, value in enumerate(update.axes)}
        raw_buttons = {
            f"button_{index}": float(value) for index, value in enumerate(update.buttons)
        }
        axes = {
            name: float(update.axes[index])
            for index, name in enumerate(WEB_AXIS_NAMES)
            if index < len(update.axes)
        }
        buttons = {
            name: bool(update.buttons[index] >= 0.5)
            for index, name in enumerate(WEB_BUTTON_NAMES)
            if index < len(update.buttons)
        }
        state = GamepadState(
            source=GamepadSource.WEB,
            connected=True,
            stale=False,
            device_name=update.id,
            mapping=update.mapping or None,
            axes=axes,
            buttons=buttons,
            raw_axes=raw_axes,
            raw_buttons=raw_buttons,
            deadman=buttons.get("lb", False),
        )
        with self._lock:
            self._web_state = state
            self._web_heartbeat = now

    async def _publish_loop(self) -> None:
        interval = self.settings.gamepad_publish_interval_sec
        while True:
            await asyncio.sleep(interval)
            state = self._select_state()
            with self._lock:
                previous = self._state
                self._state = state
            # Publish one final NONE state when a device is removed so WebUI
            # clients do not retain the last connected sample indefinitely.
            if (
                state.connected
                or state.source is not GamepadSource.NONE
                or previous.source is not GamepadSource.NONE
            ):
                await self._event_sink(
                    TelemetryEvent(
                        type="gamepad_state",
                        payload={"gamepad": state.model_dump(mode="json")},
                    )
                )

    def _select_state(self) -> GamepadState:
        now = monotonic()
        timeout = self.settings.gamepad_input_timeout_sec
        with self._lock:
            local = self._local_state.model_copy(deep=True) if self._local_state else None
            local_heartbeat = self._local_heartbeat
            web = self._web_state.model_copy(deep=True) if self._web_state else None
            web_heartbeat = self._web_heartbeat

        if local is not None:
            local.stale = now - local_heartbeat > timeout
            local.deadman = local.deadman and not local.stale
            return local
        if web is not None:
            web.stale = now - web_heartbeat > timeout
            web.connected = not web.stale
            web.deadman = web.deadman and not web.stale
            return web
        return GamepadState()

    def _local_reader_loop(self) -> None:
        try:
            import evdev  # type: ignore[import-not-found]
        except ImportError:
            logger.error("evdev is not installed; install the pi-sensors optional dependencies")
            return

        while not self._stop_event.is_set():
            device = None
            try:
                device = self._find_local_device(evdev)
                if device is None:
                    time.sleep(1.0)
                    continue
                self._read_local_device(evdev, device)
            except Exception:
                if not self._stop_event.is_set():
                    logger.exception("Local gamepad reader failed; retrying")
            finally:
                with self._lock:
                    self._local_state = None
                    self._local_heartbeat = 0.0
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        pass
            time.sleep(0.5)

    def _find_local_device(self, evdev):
        if self.settings.gamepad_device_path:
            return evdev.InputDevice(self.settings.gamepad_device_path)
        match = self.settings.gamepad_name_match.casefold()
        for path in evdev.list_devices():
            device = evdev.InputDevice(path)
            if match in device.name.casefold():
                return device
            device.close()
        return None

    def _read_local_device(self, evdev, device) -> None:
        axes: dict[str, float] = {}
        buttons: dict[str, bool] = {}
        raw_axes: dict[str, float] = {}
        raw_buttons: dict[str, float] = {}
        while not self._stop_event.is_set():
            readable, _, _ = select.select([device.fd], [], [], 0.05)
            if readable:
                for event in device.read():
                    if event.type == evdev.ecodes.EV_ABS:
                        name = self._event_name(evdev.ecodes, event.type, event.code)
                        info = device.absinfo(event.code)
                        trigger = name in {"ABS_Z", "ABS_RZ"}
                        normalized = normalize_axis(
                            event.value,
                            info.min,
                            info.max,
                            trigger=trigger,
                        )
                        raw_axes[name] = float(event.value)
                        axes[self._local_axis_name(name)] = normalized
                    elif event.type == evdev.ecodes.EV_KEY:
                        name = self._event_name(evdev.ecodes, event.type, event.code)
                        pressed = event.value != 0
                        raw_buttons[name] = float(event.value)
                        buttons[self._local_button_name(name)] = pressed

            state = GamepadState(
                source=GamepadSource.LOCAL,
                connected=True,
                stale=False,
                device_name=device.name,
                mapping="evdev",
                axes=dict(axes),
                buttons=dict(buttons),
                raw_axes=dict(raw_axes),
                raw_buttons=dict(raw_buttons),
                deadman=buttons.get("lb", False),
            )
            with self._lock:
                self._local_state = state
                self._local_heartbeat = monotonic()

    @staticmethod
    def _event_name(ecodes, event_type: int, code: int) -> str:
        value = ecodes.bytype[event_type].get(code, str(code))
        if isinstance(value, (list, tuple)):
            return str(value[0])
        return str(value)

    @staticmethod
    def _local_axis_name(name: str) -> str:
        return {
            "ABS_X": "left_x",
            "ABS_Y": "left_y",
            "ABS_RX": "right_x",
            "ABS_RY": "right_y",
            "ABS_Z": "left_trigger",
            "ABS_RZ": "right_trigger",
            "ABS_HAT0X": "dpad_x",
            "ABS_HAT0Y": "dpad_y",
        }.get(name, name.casefold())

    @staticmethod
    def _local_button_name(name: str) -> str:
        return {
            "BTN_SOUTH": "a",
            "BTN_A": "a",
            "BTN_EAST": "b",
            "BTN_B": "b",
            "BTN_NORTH": "x",
            "BTN_X": "x",
            "BTN_WEST": "y",
            "BTN_Y": "y",
            "BTN_TL": "lb",
            "BTN_TR": "rb",
            "BTN_SELECT": "back",
            "BTN_START": "start",
        }.get(name, name.casefold())
