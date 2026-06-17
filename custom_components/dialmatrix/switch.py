"""Switch platform for Dial Matrix — one entity per (doorbell × target) pair."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dialmatrix"


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    """Set up Dial Matrix switch entities."""
    if discovery_info is None:
        return

    data = hass.data[DOMAIN]
    entities: list[DialMatrixSwitch] = []

    for doorbell in data["doorbells"]:
        for target in data["targets"]:
            entity = DialMatrixSwitch(doorbell, target)
            entities.append(entity)
            data["entities"][(doorbell["id"], target["id"])] = entity

    async_add_entities(entities, True)


class DialMatrixSwitch(RestoreEntity, SwitchEntity):
    """A single routing cell: one doorbell → one notification target."""

    def __init__(self, doorbell: dict[str, str], target: dict[str, str]) -> None:
        self._doorbell_id: str = doorbell["id"]
        self._doorbell_name: str = doorbell["name"]
        self._target_id: str = target["id"]
        self._target_name: str = target["name"]
        self._is_on: bool = True  # Default to enabled on first install

        self._attr_unique_id = f"{DOMAIN}_{self._doorbell_id}_{self._target_id}"
        self._attr_should_poll = False
        # Explicit entity_id guarantees switch.dialmatrix_{doorbell_id}_{target_id}
        # regardless of the display name, and survives name changes.
        self.entity_id = f"switch.{DOMAIN}_{self._doorbell_id}_{self._target_id}"

    @property
    def name(self) -> str:
        return f"{self._doorbell_name} → {self._target_name}"

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "doorbell_id": self._doorbell_id,
            "doorbell_name": self._doorbell_name,
            "target_id": self._target_id,
            "target_name": self._target_name,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore previous state after HA restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
            _LOGGER.debug(
                "Restored state for %s: %s", self._attr_unique_id, last_state.state
            )
