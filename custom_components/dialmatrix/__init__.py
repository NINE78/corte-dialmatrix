"""Dial Matrix — call routing matrix for Home Assistant."""
from __future__ import annotations

import logging
from string import Template

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
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_TITLE = "notify_title"
CONF_NOTIFY_MESSAGE = "notify_message"
CONF_TTS_ENTITY = "tts_entity"
CONF_TTS_MEDIA_PLAYER = "tts_media_player"
CONF_TTS_MESSAGE = "tts_message"

_DEFAULT_NOTIFY_TITLE = "Doorbell"
_DEFAULT_NOTIFY_MESSAGE = "Someone is at the $doorbell_name door"
_DEFAULT_TTS_MESSAGE = "Someone is at the $doorbell_name door"

_DOORBELL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
    }
)

_TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        # Mobile push notification
        vol.Optional(CONF_NOTIFY_SERVICE): cv.string,
        vol.Optional(CONF_NOTIFY_TITLE, default=_DEFAULT_NOTIFY_TITLE): cv.string,
        vol.Optional(CONF_NOTIFY_MESSAGE, default=_DEFAULT_NOTIFY_MESSAGE): cv.string,
        # TTS
        vol.Optional(CONF_TTS_ENTITY): cv.string,
        vol.Optional(CONF_TTS_MEDIA_PLAYER): cv.entity_id,
        vol.Optional(CONF_TTS_MESSAGE, default=_DEFAULT_TTS_MESSAGE): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_DOORBELLS): vol.All(
                    cv.ensure_list, [_DOORBELL_SCHEMA]
                ),
                vol.Required(CONF_TARGETS): vol.All(
                    cv.ensure_list, [_TARGET_SCHEMA]
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


def _render(template_str: str, doorbell_name: str) -> str:
    """Substitute $doorbell_name / ${doorbell_name} in message templates."""
    return Template(template_str).safe_substitute(doorbell_name=doorbell_name)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Dial Matrix component."""
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    # Build a lookup dict for targets by id
    targets_by_id = {t[CONF_ID]: t for t in conf[CONF_TARGETS]}
    doorbells_by_id = {d[CONF_ID]: d for d in conf[CONF_DOORBELLS]}

    hass.data[DOMAIN] = {
        "doorbells": conf[CONF_DOORBELLS],
        "targets": conf[CONF_TARGETS],
        "targets_by_id": targets_by_id,
        "doorbells_by_id": doorbells_by_id,
        # Populated by switch platform; keyed by (doorbell_id, target_id)
        "entities": {},
    }

    hass.async_create_task(
        discovery.async_load_platform(hass, Platform.SWITCH, DOMAIN, {}, config)
    )

    async def handle_ring(call: ServiceCall) -> None:
        """Handle dialmatrix.ring — notify enabled targets and fire event."""
        doorbell_id = call.data["doorbell_id"]
        data = hass.data[DOMAIN]
        entities = data["entities"]

        doorbell = data["doorbells_by_id"].get(doorbell_id)
        if doorbell is None:
            _LOGGER.error("dialmatrix.ring: unknown doorbell_id '%s'", doorbell_id)
            return
        doorbell_name = doorbell[CONF_NAME]

        enabled_targets = [
            target_id
            for (db_id, target_id), entity in entities.items()
            if db_id == doorbell_id and entity.is_on
        ]

        # Fan-out notifications
        for target_id in enabled_targets:
            target = data["targets_by_id"].get(target_id)
            if target is None:
                continue

            # Mobile push notification
            notify_service = target.get(CONF_NOTIFY_SERVICE)
            if notify_service:
                # notify domain uses "domain/service" format, e.g. notify.mobile_app_foo
                parts = notify_service.split(".", 1)
                if len(parts) == 2:
                    svc_domain, svc_name = parts
                else:
                    svc_domain, svc_name = "notify", parts[0]

                await hass.services.async_call(
                    svc_domain,
                    svc_name,
                    {
                        "title": _render(target.get(CONF_NOTIFY_TITLE, _DEFAULT_NOTIFY_TITLE), doorbell_name),
                        "message": _render(target.get(CONF_NOTIFY_MESSAGE, _DEFAULT_NOTIFY_MESSAGE), doorbell_name),
                    },
                    blocking=False,
                )
                _LOGGER.debug("Notified '%s' for doorbell '%s'", notify_service, doorbell_id)

            # TTS
            tts_entity = target.get(CONF_TTS_ENTITY)
            tts_media_player = target.get(CONF_TTS_MEDIA_PLAYER)
            if tts_entity and tts_media_player:
                await hass.services.async_call(
                    "tts",
                    "speak",
                    {
                        "entity_id": tts_entity,
                        "media_player_entity_id": tts_media_player,
                        "message": _render(target.get(CONF_TTS_MESSAGE, _DEFAULT_TTS_MESSAGE), doorbell_name),
                    },
                    blocking=False,
                )
                _LOGGER.debug("TTS triggered for '%s' via '%s'", target_id, tts_entity)

        # Always fire the event so automations can still react
        hass.bus.async_fire(
            f"{DOMAIN}_ring",
            {
                "doorbell_id": doorbell_id,
                "doorbell_name": doorbell_name,
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
