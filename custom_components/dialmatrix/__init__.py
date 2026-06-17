"""Dial Matrix — call routing matrix for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import homeassistant.helpers.discovery as discovery

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dialmatrix"
CONF_DOORBELLS = "doorbells"
CONF_TARGETS = "targets"
CONF_ID = "id"
CONF_NAME = "name"

_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_DOORBELLS): vol.All(
                    cv.ensure_list, [_ITEM_SCHEMA]
                ),
                vol.Required(CONF_TARGETS): vol.All(
                    cv.ensure_list, [_ITEM_SCHEMA]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_RING_SCHEMA = vol.Schema(
    {
        vol.Required("doorbell_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Dial Matrix component."""
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    hass.data[DOMAIN] = {
        "doorbells": conf[CONF_DOORBELLS],
        "targets": conf[CONF_TARGETS],
        # Populated by switch platform; keyed by (doorbell_id, target_id)
        "entities": {},
    }

    hass.async_create_task(
        discovery.async_load_platform(hass, Platform.SWITCH, DOMAIN, {}, config)
    )

    async def handle_ring(call: ServiceCall) -> None:
        """Handle dialmatrix.ring — collect enabled targets and fire event."""
        doorbell_id = call.data["doorbell_id"]
        entities = hass.data[DOMAIN]["entities"]

        enabled_targets = [
            target_id
            for (db_id, target_id), entity in entities.items()
            if db_id == doorbell_id and entity.is_on
        ]

        hass.bus.async_fire(
            f"{DOMAIN}_ring",
            {
                "doorbell_id": doorbell_id,
                "enabled_targets": enabled_targets,
            },
        )

        _LOGGER.debug(
            "Ring fired for doorbell '%s' → enabled targets: %s",
            doorbell_id,
            enabled_targets,
        )

    hass.services.async_register(
        DOMAIN, "ring", handle_ring, schema=_RING_SCHEMA
    )

    return True
