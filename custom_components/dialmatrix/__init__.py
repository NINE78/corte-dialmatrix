"""Dial Matrix — call routing matrix for Home Assistant.

Routes events from *sources* (doorbells, Frigate camera detections such as
person / car) to *targets* (mobile push notifications, TTS speakers) based on
a matrix of switch entities that can be toggled from the companion card.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import logging
from string import Template
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
import homeassistant.helpers.discovery as discovery

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dialmatrix"

# Event types (row kinds in the matrix)
EVENT_TYPE_DOORBELL = "doorbell"
DEFAULT_LABELS = ["person", "car"]

# Config keys
CONF_DOORBELLS = "doorbells"
CONF_CAMERAS = "cameras"
CONF_TARGETS = "targets"
CONF_FRIGATE = "frigate"
CONF_ID = "id"
CONF_NAME = "name"
CONF_FRIGATE_CAMERA = "frigate_camera"
CONF_LABELS = "labels"
CONF_ZONES = "zones"
CONF_MQTT = "mqtt"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_IMAGE_URL = "image_url"

CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_TITLE = "notify_title"
CONF_NOTIFY_MESSAGE = "notify_message"
CONF_NOTIFY_DATA = "notify_data"
CONF_DETECT_TITLE = "detect_title"
CONF_DETECT_MESSAGE = "detect_message"
CONF_TTS_ENTITY = "tts_entity"
CONF_TTS_MEDIA_PLAYER = "tts_media_player"
CONF_TTS_MESSAGE = "tts_message"
CONF_DETECT_TTS_MESSAGE = "detect_tts_message"

_DEFAULT_NOTIFY_TITLE = "Doorbell"
_DEFAULT_NOTIFY_MESSAGE = "Someone is at the $doorbell_name door"
_DEFAULT_TTS_MESSAGE = "Someone is at the $doorbell_name door"
_DEFAULT_DETECT_TITLE = "$label_title detected"
_DEFAULT_DETECT_MESSAGE = "$label_title detected at $camera_name"
_DEFAULT_DETECT_TTS_MESSAGE = "A $label was detected at the $camera_name"
_DEFAULT_FRIGATE_TOPIC = "frigate/events"
_DEFAULT_FRIGATE_IMAGE_URL = "/api/frigate/notifications/$event_id/thumbnail.jpg"

# Bound on remembered Frigate event ids (dedup of MQTT new/update messages)
_SEEN_EVENTS_MAX = 1000

_DOORBELL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        # If set, a message on this MQTT topic rings the doorbell. The payload
        # (raw string, or JSON with "event_id"/"id") is used as Frigate event id.
        vol.Optional(CONF_MQTT_TOPIC): cv.string,
    }
)

_CAMERA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        # Frigate camera name as it appears in MQTT payloads; defaults to id
        vol.Optional(CONF_FRIGATE_CAMERA): cv.string,
        # Object labels that get their own matrix row for this camera
        vol.Optional(CONF_LABELS, default=DEFAULT_LABELS): vol.All(
            cv.ensure_list, [cv.string]
        ),
        # If set, only events that entered one of these Frigate zones dispatch.
        # Either a list (applies to every label) or a mapping label -> zones.
        vol.Optional(CONF_ZONES, default={}): vol.Any(
            vol.Schema({cv.string: vol.All(cv.ensure_list, [cv.string])}),
            vol.All(cv.ensure_list, [cv.string], lambda v: {"*": v}),
        ),
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
        vol.Optional(CONF_DETECT_TITLE, default=_DEFAULT_DETECT_TITLE): cv.string,
        vol.Optional(CONF_DETECT_MESSAGE, default=_DEFAULT_DETECT_MESSAGE): cv.string,
        # Extra `data:` merged into every notify call for this target
        vol.Optional(CONF_NOTIFY_DATA, default={}): dict,
        # TTS
        vol.Optional(CONF_TTS_ENTITY): cv.string,
        vol.Optional(CONF_TTS_MEDIA_PLAYER): cv.entity_id,
        vol.Optional(CONF_TTS_MESSAGE, default=_DEFAULT_TTS_MESSAGE): cv.string,
        vol.Optional(
            CONF_DETECT_TTS_MESSAGE, default=_DEFAULT_DETECT_TTS_MESSAGE
        ): cv.string,
    }
)

_FRIGATE_SCHEMA = vol.Schema(
    {
        # Subscribe to Frigate's MQTT event stream and dispatch automatically
        vol.Optional(CONF_MQTT, default=True): cv.boolean,
        vol.Optional(CONF_MQTT_TOPIC, default=_DEFAULT_FRIGATE_TOPIC): cv.string,
        # Image attached to push notifications; "" disables. Supports $event_id
        vol.Optional(CONF_IMAGE_URL, default=_DEFAULT_FRIGATE_IMAGE_URL): cv.string,
    }
)


def _at_least_one_source(conf: dict) -> dict:
    if not conf.get(CONF_DOORBELLS) and not conf.get(CONF_CAMERAS):
        raise vol.Invalid("dialmatrix needs at least one of 'doorbells' or 'cameras'")
    return conf


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_DOORBELLS, default=[]): vol.All(
                        cv.ensure_list, [_DOORBELL_SCHEMA]
                    ),
                    vol.Optional(CONF_CAMERAS, default=[]): vol.All(
                        cv.ensure_list, [_CAMERA_SCHEMA]
                    ),
                    vol.Required(CONF_TARGETS): vol.All(
                        cv.ensure_list, [_TARGET_SCHEMA]
                    ),
                    vol.Optional(CONF_FRIGATE, default={}): _FRIGATE_SCHEMA,
                }
            ),
            _at_least_one_source,
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_RING_SCHEMA = vol.Schema(
    {
        vol.Required("doorbell_id"): cv.string,
        # Optional Frigate event id, used to attach a snapshot to the push
        vol.Optional("event_id"): cv.string,
    }
)

_DETECT_SCHEMA = vol.Schema(
    {
        vol.Required("camera_id"): cv.string,
        vol.Required("label"): cv.string,
        vol.Optional("event_id"): cv.string,
        vol.Optional("sub_label"): cv.string,
        vol.Optional("zones", default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)


def _render(template_str: str, ctx: dict[str, Any]) -> str:
    """Substitute $placeholders (e.g. $doorbell_name, $camera_name, $label)."""
    return Template(template_str).safe_substitute(ctx)


def _required_zones(camera: dict[str, Any], label: str) -> list[str]:
    """Zones an object must be in before dispatching, for this camera/label."""
    zones = camera[CONF_ZONES]
    return zones.get(label) or zones.get("*") or []


def _event_id_from_payload(payload: Any) -> str | None:
    """Extract a Frigate event id from a doorbell MQTT payload.

    Accepts a bare event id string, or a JSON object with "event_id" / "id".
    """
    if payload is None:
        return None
    text = str(payload).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict):
        value = parsed.get("event_id") or parsed.get("id")
        return str(value) if value else None
    if isinstance(parsed, str) and parsed:
        return parsed
    return text


def _normalise_sub_label(value: Any) -> str | None:
    """Frigate sends sub_label as a string or as [name, score]."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


def _build_sources(conf: dict) -> list[dict[str, Any]]:
    """Flatten doorbells and camera×label pairs into a single ordered list.

    Each source dict has: event_type, id, name, attributes (extra state attrs
    for the switch) and order (for stable sorting in the card).
    """
    sources: list[dict[str, Any]] = []
    for idx, doorbell in enumerate(conf[CONF_DOORBELLS]):
        sources.append(
            {
                "event_type": EVENT_TYPE_DOORBELL,
                "id": doorbell[CONF_ID],
                "name": doorbell[CONF_NAME],
                "order": (0, idx),
                "attributes": {
                    # Legacy attribute names, kept for older cards/automations
                    "doorbell_id": doorbell[CONF_ID],
                    "doorbell_name": doorbell[CONF_NAME],
                },
            }
        )
    for cam_idx, camera in enumerate(conf[CONF_CAMERAS]):
        for label_idx, label in enumerate(camera[CONF_LABELS]):
            sources.append(
                {
                    "event_type": label,
                    "id": camera[CONF_ID],
                    "name": camera[CONF_NAME],
                    "order": (1 + label_idx, cam_idx),
                    "attributes": {
                        "camera_id": camera[CONF_ID],
                        "camera_name": camera[CONF_NAME],
                        "label": label,
                    },
                }
            )
    return sources


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Dial Matrix component."""
    conf = config.get(DOMAIN)
    if conf is None:
        return True

    targets_by_id = {t[CONF_ID]: t for t in conf[CONF_TARGETS]}
    doorbells_by_id = {d[CONF_ID]: d for d in conf[CONF_DOORBELLS]}
    cameras_by_id = {c[CONF_ID]: c for c in conf[CONF_CAMERAS]}
    cameras_by_frigate_name = {
        c.get(CONF_FRIGATE_CAMERA, c[CONF_ID]): c for c in conf[CONF_CAMERAS]
    }
    frigate_conf = conf[CONF_FRIGATE]

    hass.data[DOMAIN] = {
        "doorbells": conf[CONF_DOORBELLS],
        "cameras": conf[CONF_CAMERAS],
        "targets": conf[CONF_TARGETS],
        "sources": _build_sources(conf),
        "targets_by_id": targets_by_id,
        "doorbells_by_id": doorbells_by_id,
        "cameras_by_id": cameras_by_id,
        # Populated by switch platform; keyed by (event_type, source_id, target_id)
        "entities": {},
    }

    hass.async_create_task(
        discovery.async_load_platform(hass, Platform.SWITCH, DOMAIN, {}, config)
    )

    # -------------------------------------------------------------------------
    # Fan-out
    # -------------------------------------------------------------------------

    async def _notify_target(
        target: dict[str, Any],
        title_key: str,
        message_key: str,
        tts_key: str,
        ctx: dict[str, Any],
        notify_data: dict[str, Any],
    ) -> None:
        """Send push + TTS for a single target."""
        notify_service = target.get(CONF_NOTIFY_SERVICE)
        if notify_service:
            # notify domain uses "domain.service" format, e.g. notify.mobile_app_foo
            parts = notify_service.split(".", 1)
            if len(parts) == 2:
                svc_domain, svc_name = parts
            else:
                svc_domain, svc_name = "notify", parts[0]

            payload: dict[str, Any] = {
                "title": _render(target[title_key], ctx),
                "message": _render(target[message_key], ctx),
            }
            data = {**notify_data, **target.get(CONF_NOTIFY_DATA, {})}
            if data:
                payload["data"] = data

            await hass.services.async_call(
                svc_domain, svc_name, payload, blocking=False
            )
            _LOGGER.debug(
                "Notified '%s' for %s '%s'",
                notify_service,
                ctx["event_type"],
                ctx["source_id"],
            )

        tts_entity = target.get(CONF_TTS_ENTITY)
        tts_media_player = target.get(CONF_TTS_MEDIA_PLAYER)
        if tts_entity and tts_media_player:
            await hass.services.async_call(
                "tts",
                "speak",
                {
                    "entity_id": tts_entity,
                    "media_player_entity_id": tts_media_player,
                    "message": _render(target[tts_key], ctx),
                },
                blocking=False,
            )
            _LOGGER.debug("TTS triggered for '%s' via '%s'", target[CONF_ID], tts_entity)

    async def _dispatch(
        event_type: str,
        source_id: str,
        ctx: dict[str, Any],
        title_key: str,
        message_key: str,
        tts_key: str,
        notify_data: dict[str, Any] | None = None,
    ) -> list[str]:
        """Notify all enabled targets for (event_type, source_id); return their ids."""
        data = hass.data[DOMAIN]
        enabled_targets = [
            target_id
            for (ev, src, target_id), entity in data["entities"].items()
            if ev == event_type and src == source_id and entity.is_on
        ]

        for target_id in enabled_targets:
            target = targets_by_id.get(target_id)
            if target is None:
                continue
            await _notify_target(
                target, title_key, message_key, tts_key, ctx, notify_data or {}
            )

        return enabled_targets

    def _frigate_notify_data(event_id: str | None, ctx: dict[str, Any]) -> dict[str, Any]:
        """Push `data:` derived from a Frigate event id: snapshot image + tag."""
        if not event_id:
            return {}
        # Collapse repeated pushes for the same Frigate event on the phone
        data: dict[str, Any] = {"tag": f"{DOMAIN}_{event_id}"}
        image_url = frigate_conf.get(CONF_IMAGE_URL, "")
        if image_url:
            data["image"] = _render(image_url, ctx)
        return data

    # -------------------------------------------------------------------------
    # Doorbell ring
    # -------------------------------------------------------------------------

    async def async_ring(doorbell_id: str, event_id: str | None = None) -> None:
        doorbell = doorbells_by_id.get(doorbell_id)
        if doorbell is None:
            _LOGGER.error("dialmatrix.ring: unknown doorbell_id '%s'", doorbell_id)
            return
        doorbell_name = doorbell[CONF_NAME]

        ctx = {
            "event_type": EVENT_TYPE_DOORBELL,
            "source_id": doorbell_id,
            "source_name": doorbell_name,
            "doorbell_id": doorbell_id,
            "doorbell_name": doorbell_name,
            "event_id": event_id or "",
        }
        enabled_targets = await _dispatch(
            EVENT_TYPE_DOORBELL,
            doorbell_id,
            ctx,
            CONF_NOTIFY_TITLE,
            CONF_NOTIFY_MESSAGE,
            CONF_TTS_MESSAGE,
            _frigate_notify_data(event_id, ctx),
        )

        # Always fire the event so automations can still react
        hass.bus.async_fire(
            f"{DOMAIN}_ring",
            {
                "doorbell_id": doorbell_id,
                "doorbell_name": doorbell_name,
                "event_id": event_id,
                "enabled_targets": enabled_targets,
            },
        )
        _LOGGER.debug(
            "Ring fired for doorbell '%s' → enabled targets: %s",
            doorbell_id,
            enabled_targets,
        )

    async def handle_ring(call: ServiceCall) -> None:
        """Handle dialmatrix.ring — notify enabled targets and fire event."""
        await async_ring(call.data["doorbell_id"], call.data.get("event_id"))

    # -------------------------------------------------------------------------
    # Frigate detection (person / car / ...)
    # -------------------------------------------------------------------------

    async def async_detect(
        camera: dict[str, Any],
        label: str,
        event_id: str | None = None,
        sub_label: str | None = None,
        zones: list[str] | None = None,
    ) -> None:
        camera_id = camera[CONF_ID]
        camera_name = camera[CONF_NAME]
        zones = zones or []

        if label not in camera[CONF_LABELS]:
            _LOGGER.warning(
                "dialmatrix.detect: label '%s' is not configured for camera '%s' "
                "(labels: %s)",
                label,
                camera_id,
                camera[CONF_LABELS],
            )
            return

        ctx = {
            "event_type": label,
            "source_id": camera_id,
            "source_name": camera_name,
            "camera_id": camera_id,
            "camera_name": camera_name,
            "label": label,
            "label_title": label.replace("_", " ").capitalize(),
            "sub_label": sub_label or "",
            "event_id": event_id or "",
            "zones": ", ".join(zones),
        }

        enabled_targets = await _dispatch(
            label,
            camera_id,
            ctx,
            CONF_DETECT_TITLE,
            CONF_DETECT_MESSAGE,
            CONF_DETECT_TTS_MESSAGE,
            _frigate_notify_data(event_id, ctx),
        )

        hass.bus.async_fire(
            f"{DOMAIN}_detect",
            {
                "camera_id": camera_id,
                "camera_name": camera_name,
                "label": label,
                "sub_label": sub_label,
                "event_id": event_id,
                "zones": zones,
                "enabled_targets": enabled_targets,
            },
        )
        _LOGGER.debug(
            "Detect fired for camera '%s' label '%s' → enabled targets: %s",
            camera_id,
            label,
            enabled_targets,
        )

    async def handle_detect(call: ServiceCall) -> None:
        """Handle dialmatrix.detect — notify enabled targets and fire event."""
        camera = cameras_by_id.get(call.data["camera_id"])
        if camera is None:
            _LOGGER.error(
                "dialmatrix.detect: unknown camera_id '%s'", call.data["camera_id"]
            )
            return
        await async_detect(
            camera,
            call.data["label"],
            event_id=call.data.get("event_id"),
            sub_label=call.data.get("sub_label"),
            zones=call.data.get("zones"),
        )

    hass.services.async_register(DOMAIN, "ring", handle_ring, schema=_RING_SCHEMA)
    hass.services.async_register(
        DOMAIN, "detect", handle_detect, schema=_DETECT_SCHEMA
    )

    # -------------------------------------------------------------------------
    # MQTT: doorbell topics and Frigate's event stream, so no automations are
    # needed for either.
    # -------------------------------------------------------------------------

    def _make_doorbell_handler(doorbell: dict[str, Any]):
        @callback
        def _on_doorbell_message(msg: Any) -> None:
            event_id = _event_id_from_payload(msg.payload)
            _LOGGER.debug(
                "MQTT ring for doorbell '%s' (event_id=%s)", doorbell[CONF_ID], event_id
            )
            hass.async_create_task(async_ring(doorbell[CONF_ID], event_id))

        return _on_doorbell_message

    seen_events: OrderedDict[str, None] = OrderedDict()

    def _remember(event_id: str) -> None:
        seen_events[event_id] = None
        while len(seen_events) > _SEEN_EVENTS_MAX:
            seen_events.popitem(last=False)

    @callback
    def _on_frigate_message(msg: Any) -> None:
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.debug("Ignoring non-JSON Frigate payload on %s", msg.topic)
            return
        if not isinstance(payload, dict):
            return

        after = payload.get("after") or {}
        event_id = after.get("id")
        if not event_id or payload.get("type") == "end":
            return
        if event_id in seen_events or after.get("false_positive"):
            return

        camera = cameras_by_frigate_name.get(after.get("camera"))
        if camera is None:
            return
        label = after.get("label")
        if label not in camera[CONF_LABELS]:
            return

        zones = list(
            dict.fromkeys(
                (after.get("entered_zones") or []) + (after.get("current_zones") or [])
            )
        )
        required_zones = _required_zones(camera, label)
        if required_zones and not set(zones).intersection(required_zones):
            # Not in a zone we care about (yet) — a later "update" may qualify
            return

        _remember(event_id)
        hass.async_create_task(
            async_detect(
                camera,
                label,
                event_id=event_id,
                sub_label=_normalise_sub_label(after.get("sub_label")),
                zones=zones,
            )
        )

    subscriptions: list[tuple[str, Any]] = []
    for doorbell in conf[CONF_DOORBELLS]:
        if doorbell.get(CONF_MQTT_TOPIC):
            subscriptions.append(
                (doorbell[CONF_MQTT_TOPIC], _make_doorbell_handler(doorbell))
            )
    if conf[CONF_CAMERAS] and frigate_conf[CONF_MQTT]:
        subscriptions.append((frigate_conf[CONF_MQTT_TOPIC], _on_frigate_message))

    async def _async_subscribe_mqtt() -> None:
        from homeassistant.components import mqtt  # pylint: disable=import-outside-toplevel

        topics = [topic for topic, _ in subscriptions]
        try:
            ready = await mqtt.async_wait_for_mqtt_client(hass)
        except Exception:  # pylint: disable=broad-except
            ready = False
        if not ready:
            _LOGGER.warning(
                "MQTT is not available; topics %s will not be handled. Use the "
                "dialmatrix.ring / dialmatrix.detect services from automations, "
                "or remove the mqtt options to silence this.",
                topics,
            )
            return

        for topic, handler in subscriptions:
            await mqtt.async_subscribe(hass, topic, handler)
        _LOGGER.info("Subscribed to MQTT topics %s", topics)

    if subscriptions:
        hass.async_create_task(_async_subscribe_mqtt())

    return True
