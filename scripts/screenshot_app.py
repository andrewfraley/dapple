"""The app as scripts/screenshot.sh serves it: every strand answers as if it's on.

The throwaway instance has no strands to ask, so without this the power switch
would read "Not answering" in every screenshot. The tree also starts out showing
the built-in Halloween preset, so the page describes something rather than
"Nothing applied yet".

    DAPPLE_DATA_DIR=/tmp/x uvicorn --app-dir scripts screenshot_app:app
"""

import os
from pathlib import Path

from app.config import STATE_FILENAME
from app.devices import DeviceManager
from app.main import app  # noqa: F401 — what uvicorn serves
from app.models import DeviceInfo, Pattern, Slot
from app.presets import default_presets
from app.state import StateStore

BRIGHTNESS = 60


async def _every_strand_on(self, group_id=None):
    devices = self.group(group_id).devices if group_id else self.devices
    return self._with_groups(
        [
            DeviceInfo(
                name=device.name,
                host=device.host,
                reachable=True,
                number_of_led=device.config.number_of_led,
                led_profile=device.config.led_profile,
                mode="movie",
                brightness=BRIGHTNESS,
            )
            for device in devices
        ]
    )


DeviceManager.live_state = _every_strand_on

# Halloween's 4:1, scaled up: the preset stores it as weights 4 and 1, which
# would leave both share sliders sitting at the far left.
halloween = default_presets()["Halloween"]
StateStore(Path(os.environ["DAPPLE_DATA_DIR"]) / STATE_FILENAME).record(
    "tree",
    Pattern(
        slots=[Slot(rgbw=slot.rgbw, weight=slot.weight * 20) for slot in halloween.slots],
        brightness=BRIGHTNESS,
    ),
    preset="Halloween",
)
