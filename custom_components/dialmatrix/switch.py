"""Switch platform for Dial Matrix — one entity per (source × target) pair.

A *source* is either a doorbell or a Frigate camera × label (person, car, …).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, EVENT_TYPE_DOORBELL

_LOGGER = logging.getLogger(__name__)

_ICONS = {
    EVENT_TYPE_DOORBELL: "mdi:doorbell",
    "person": "mdi:walk",
    "car": "mdi:car",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dial Matrix switch entities from a config entry."""
    runtime = hass.data[DOMAIN]["runtime"]
    entities: list[DialMatrixSwitch] = []

    for source in runtime.sources:
        for target_idx, target in enumerate(runtime.targets):
            entity = DialMatrixSwitch(entry, source, target, target_idx)
            entities.append(entity)
            runtime.entities[(source["event_type"], source["id"], target["id"])] = entity

    registry = er.async_get(hass)

    # Drop registry entries for cells that no longer exist (removed doorbell,
    # camera, label or target) so they don't linger as unavailable entities.
    expected = {e.unique_id for e in entities}
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id not in expected:
            registry.async_remove(reg_entry.entity_id)
            _LOGGER.debug("Removed stale entity %s", reg_entry.entity_id)

    async_add_entities(entities, True)

    # Ensure entity IDs in the registry match the desired scheme. The registry
    # wins over self.entity_id, so we update it directly.
    for entity in entities:
        desired_id = f"switch.{entity.slug}"
        current_id = registry.async_get_entity_id("switch", DOMAIN, entity.unique_id)
        if current_id and current_id != desired_id:
            registry.async_update_entity(current_id, new_entity_id=desired_id)
            _LOGGER.debug("Renamed entity %s → %s", current_id, desired_id)


class DialMatrixSwitch(RestoreEntity, SwitchEntity):
    """A single routing cell: one source (doorbell / detection) → one target."""

    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        source: dict[str, Any],
        target: dict[str, str],
        target_idx: int,
    ) -> None:
        self._event_type: str = source["event_type"]
        self._source_id: str = source["id"]
        self._source_name: str = source["name"]
        self._source_attrs: dict[str, Any] = source.get("attributes", {})
        self._order: tuple[int, ...] = (*source.get("order", (0, 0)), target_idx)
        self._target_id: str = target["id"]
        self._target_name: str = target["name"]
        self._is_on: bool = True  # Default to enabled on first install

        # Doorbell rows keep the historical id scheme so existing entities and
        # dashboards survive the upgrade: dialmatrix_{doorbell}_{target}.
        if self._event_type == EVENT_TYPE_DOORBELL:
            self.slug = f"{DOMAIN}_{self._source_id}_{self._target_id}"
        else:
            self.slug = (
                f"{DOMAIN}_{self._source_id}_{self._event_type}_{self._target_id}"
            )

        self._attr_unique_id = self.slug
        self._attr_icon = _ICONS.get(self._event_type, "mdi:motion-sensor")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Dial Matrix",
            manufacturer="Dial Matrix",
            entry_type=None,
        )

    @property
    def name(self) -> str:
        if self._event_type == EVENT_TYPE_DOORBELL:
            return f"{self._source_name} → {self._target_name}"
        label = self._event_type.replace("_", " ").capitalize()
        return f"{self._source_name} {label} → {self._target_name}"

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "event_type": self._event_type,
            "source_id": self._source_id,
            "source_name": self._source_name,
            **self._source_attrs,
            "target_id": self._target_id,
            "target_name": self._target_name,
            "sort_order": list(self._order),
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
