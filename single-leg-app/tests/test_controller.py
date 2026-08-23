import asyncio

from single_leg_server.config import Settings
from single_leg_server.controller import SingleLegController
from single_leg_server.models import ConnectionState, ControlMode


def test_emulated_controller_exposes_only_one_leg_and_two_axes() -> None:
    async def exercise() -> None:
        events = []

        async def sink(event) -> None:
            events.append(event)

        controller = SingleLegController(Settings(emulate_devices=True), sink)
        await controller.start()
        try:
            await asyncio.sleep(0.06)
            actuators = controller.list_actuators()
            assert [item.label for item in actuators] == ["hip", "knee"]
            assert controller.status.connection_state is ConnectionState.EMULATED

            updated = await controller.set_target(0, ControlMode.POSITION, 2600)
            assert updated.target_position == 2600
            assert all(
                "sensor" not in key
                for event in events
                for key in event.payload
            )
        finally:
            await controller.stop()

    asyncio.run(exercise())

