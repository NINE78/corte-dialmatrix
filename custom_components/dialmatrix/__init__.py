"""Dial Matrix — call routing matrix for Home Assistant.

Routes events from *sources* (doorbells, Frigate camera detections such as
person / car) to *targets* (mobile push notifications, TTS speakers) based on
a matrix of switch entities that can be toggled from the companion card.

Configuration lives in a config entry (Settings → Integrations → Dial Matrix →
Configure). A legacy `dialmatrix:` block in configuration.yaml is imported
once and can then be removed.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import json
import logging
from string import Template
from typing import Any
from urllib.parse import urlencode

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CAMERAS,
    CONF_DETECT_MESSAGE,
    CONF_DETECT_TITLE,
    CONF_DETECT_TTS_MESSAGE,
    CONF_DOORBELLS,
    CONF_FRIGATE,
    CONF_FRIGATE_CAMERA,
    CONF_ID,
    CONF_IMAGE_URL,
    CONF_LABELS,
    CONF_MQTT,
    CONF_MQTT_TOPIC,
    CONF_NAME,
    CONF_NOTIFY_DATA,
    CONF_NOTIFY_MESSAGE,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE,
    CONF_TARGETS,
    CONF_TTS_ANNOUNCE,
    CONF_TTS_ENTITY,
    CONF_TTS_MEDIA_PLAYER,
    CONF_TTS_MESSAGE,
    CONF_TTS_VOLUME,
    CONF_ZONES,
    DEFAULT_DETECT_MESSAGE,
    DEFAULT_DETECT_TITLE,
    DEFAULT_DETECT_TTS_MESSAGE,
    DEFAULT_FRIGATE_IMAGE_URL,
    DEFAULT_FRIGATE_TOPIC,
    DEFAULT_LABEL_ICON,
    DEFAULT_LABELS,
    DEFAULT_NOTIFY_MESSAGE,
    DEFAULT_NOTIFY_TITLE,
    DEFAULT_TTS_MESSAGE,
    DOMAIN,
    DOORBELL_ICON,
    EVENT_TYPE_DOORBELL,
    LABEL_ICONS,
    LABEL_OPTIONS,
    MEDIA_PLAYER_FEATURE_ANNOUNCE,
    SEEN_EVENTS_MAX,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]

# -----------------------------------------------------------------------------
# Schemas — shared by YAML import and by the options flow (defaults applied here)
# -----------------------------------------------------------------------------

DOORBELL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        # If set, a message on this MQTT topic rings the doorbell. The payload
        # (raw string, or JSON with "event_id"/"id") is used as Frigate event id.
        vol.Optional(CONF_MQTT_TOPIC): cv.string,
    }
)

CAMERA_SCHEMA = vol.Schema(
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

TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        # Mobile push notification
        vol.Optional(CONF_NOTIFY_SERVICE): cv.string,
        vol.Optional(CONF_NOTIFY_TITLE, default=DEFAULT_NOTIFY_TITLE): cv.string,
        vol.Optional(CONF_NOTIFY_MESSAGE, default=DEFAULT_NOTIFY_MESSAGE): cv.string,
        vol.Optional(CONF_DETECT_TITLE, default=DEFAULT_DETECT_TITLE): cv.string,
        vol.Optional(CONF_DETECT_MESSAGE, default=DEFAULT_DETECT_MESSAGE): cv.string,
        # Extra `data:` merged into every notify call for this target
        vol.Optional(CONF_NOTIFY_DATA, default={}): dict,
        # TTS
        vol.Optional(CONF_TTS_ENTITY): cv.string,
        # One media player or a list; announcements go to all of them
        vol.Optional(CONF_TTS_MEDIA_PLAYER): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(CONF_TTS_MESSAGE, default=DEFAULT_TTS_MESSAGE): cv.string,
        vol.Optional(
            CONF_DETECT_TTS_MESSAGE, default=DEFAULT_DETECT_TTS_MESSAGE
        ): cv.string,
        # Announcement mode: duck / pause the music, speak, resume at the old
        # level (Sonos and other players with the "announce" feature).
        vol.Optional(CONF_TTS_ANNOUNCE, default=True): cv.boolean,
        # Volume (0-100) for the announcement; None = player's current volume
        vol.Optional(CONF_TTS_VOLUME): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)

FRIGATE_SCHEMA = vol.Schema(
    {
        # Subscribe to Frigate's MQTT event stream and dispatch automatically
        vol.Optional(CONF_MQTT, default=True): cv.boolean,
        vol.Optional(CONF_MQTT_TOPIC, default=DEFAULT_FRIGATE_TOPIC): cv.string,
        # Image attached to push notifications; "" disables. Supports $event_id
        vol.Optional(CONF_IMAGE_URL, default=DEFAULT_FRIGATE_IMAGE_URL): cv.string,
    }
)

def _unique_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for item in items:
        if item[CONF_ID] in seen:
            raise vol.Invalid(f"duplicate id '{item[CONF_ID]}'")
        seen.add(item[CONF_ID])
    return items


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DOORBELLS, default=[]): vol.All(
            cv.ensure_list, [DOORBELL_SCHEMA], _unique_ids
        ),
        vol.Optional(CONF_CAMERAS, default=[]): vol.All(
            cv.ensure_list, [CAMERA_SCHEMA], _unique_ids
        ),
        vol.Optional(CONF_TARGETS, default=[]): vol.All(
            cv.ensure_list, [TARGET_SCHEMA], _unique_ids
        ),
        vol.Optional(CONF_FRIGATE, default={}): FRIGATE_SCHEMA,
    },
    extra=vol.REMOVE_EXTRA,
)

# `dialmatrix:` with nothing under it (e.g. everything commented out after the
# import) is accepted and ignored.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Any(None, OPTIONS_SCHEMA)}, extra=vol.ALLOW_EXTRA
)

RING_SCHEMA = vol.Schema(
    {
        vol.Required("doorbell_id"): cv.string,
        # Optional Frigate event id, used to attach a snapshot to the push
        vol.Optional("event_id"): cv.string,
    }
)

DETECT_SCHEMA = vol.Schema(
    {
        vol.Required("camera_id"): cv.string,
        vol.Required("label"): cv.string,
        vol.Optional("event_id"): cv.string,
        vol.Optional("sub_label"): cv.string,
        vol.Optional("zones", default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# Texts that were the defaults before the $icon placeholder existed. Entries
# created back then store them literally; upgrade them to the current defaults
# so the icons appear without re-typing every title.
_LEGACY_DEFAULTS = {
    CONF_NOTIFY_TITLE: {"Doorbell": DEFAULT_NOTIFY_TITLE},
    CONF_DETECT_TITLE: {"$label_title detected": DEFAULT_DETECT_TITLE},
    CONF_DETECT_MESSAGE: {"$label_title detected at $camera_name": DEFAULT_DETECT_MESSAGE},
}


def _upgrade_legacy_defaults(conf: dict[str, Any]) -> dict[str, Any]:
    for target in conf.get(CONF_TARGETS, []):
        for key, mapping in _LEGACY_DEFAULTS.items():
            if target.get(key) in mapping:
                target[key] = mapping[target[key]]
    return conf



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


def build_sources(conf: dict) -> list[dict[str, Any]]:
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


# -----------------------------------------------------------------------------
# Runtime — one per config entry (single instance)
# -----------------------------------------------------------------------------


class DialMatrixRuntime:
    """Holds the validated config, the switch entities and the MQTT wiring."""

    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        self.hass = hass
        self.conf = conf
        self.doorbells: list[dict[str, Any]] = conf[CONF_DOORBELLS]
        self.cameras: list[dict[str, Any]] = conf[CONF_CAMERAS]
        self.targets: list[dict[str, Any]] = conf[CONF_TARGETS]
        self.frigate: dict[str, Any] = conf[CONF_FRIGATE]
        self.sources = build_sources(conf)

        self.targets_by_id = {t[CONF_ID]: t for t in self.targets}
        self.doorbells_by_id = {d[CONF_ID]: d for d in self.doorbells}
        self.cameras_by_id = {c[CONF_ID]: c for c in self.cameras}
        self.cameras_by_frigate_name = {
            c.get(CONF_FRIGATE_CAMERA) or c[CONF_ID]: c for c in self.cameras
        }

        # Populated by the switch platform; keyed by (event_type, source_id, target_id)
        self.entities: dict[tuple[str, str, str], Any] = {}

        self._seen_events: OrderedDict[str, None] = OrderedDict()
        self._unsubscribe: list[Callable[[], None]] = []

    # -- fan-out ---------------------------------------------------------------

    async def _notify_target(
        self,
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

            await self.hass.services.async_call(
                svc_domain, svc_name, payload, blocking=False
            )
            _LOGGER.debug(
                "Notified '%s' for %s '%s'",
                notify_service,
                ctx["event_type"],
                ctx["source_id"],
            )

        tts_entity = target.get(CONF_TTS_ENTITY)
        players = target.get(CONF_TTS_MEDIA_PLAYER) or []
        if tts_entity and players:
            await self._speak(target, tts_entity, players, _render(target[tts_key], ctx))

    def _supports_announce(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        features = state.attributes.get("supported_features") or 0
        return bool(int(features) & MEDIA_PLAYER_FEATURE_ANNOUNCE)

    async def _speak(
        self, target: dict[str, Any], tts_entity: str, players: list[str], message: str
    ) -> None:
        """Speak `message` on the target's players.

        Players that support announcements (Sonos, …) get play_media with
        announce=True: the player ducks or pauses the current stream, plays the
        clip at `tts_volume`, and resumes at the previous level by itself. The
        rest get a plain tts.speak.
        """
        announce_players: list[str] = []
        plain_players: list[str] = []
        for player in players:
            if target.get(CONF_TTS_ANNOUNCE, True) and self._supports_announce(player):
                announce_players.append(player)
            else:
                plain_players.append(player)

        if announce_players:
            data: dict[str, Any] = {
                "entity_id": announce_players,
                "media_content_id": f"media-source://tts/{tts_entity}?"
                + urlencode({"message": message}),
                "media_content_type": "music",
                "announce": True,
            }
            volume = target.get(CONF_TTS_VOLUME)
            if volume is not None:
                data["extra"] = {"volume": volume}
            await self.hass.services.async_call(
                "media_player", "play_media", data, blocking=False
            )
            _LOGGER.debug(
                "Announcement for '%s' on %s via '%s'", target[CONF_ID], announce_players, tts_entity
            )

        if plain_players:
            await self.hass.services.async_call(
                "tts",
                "speak",
                {
                    "entity_id": tts_entity,
                    "media_player_entity_id": plain_players,
                    "message": message,
                },
                blocking=False,
            )
            _LOGGER.debug("TTS for '%s' on %s via '%s'", target[CONF_ID], plain_players, tts_entity)

    async def _dispatch(
        self,
        event_type: str,
        source_id: str,
        ctx: dict[str, Any],
        title_key: str,
        message_key: str,
        tts_key: str,
        notify_data: dict[str, Any] | None = None,
    ) -> list[str]:
        """Notify all enabled targets for (event_type, source_id); return their ids."""
        enabled_targets = [
            target_id
            for (ev, src, target_id), entity in self.entities.items()
            if ev == event_type and src == source_id and entity.is_on
        ]

        for target_id in enabled_targets:
            target = self.targets_by_id.get(target_id)
            if target is None:
                continue
            await self._notify_target(
                target, title_key, message_key, tts_key, ctx, notify_data or {}
            )

        return enabled_targets

    def _frigate_notify_data(
        self, event_id: str | None, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """Push `data:` derived from a Frigate event id: snapshot image + tag."""
        if not event_id:
            return {}
        # Collapse repeated pushes for the same Frigate event on the phone
        data: dict[str, Any] = {"tag": f"{DOMAIN}_{event_id}"}
        image_url = self.frigate.get(CONF_IMAGE_URL, "")
        if image_url:
            data["image"] = _render(image_url, ctx)
        return data

    # -- doorbell ring ---------------------------------------------------------

    async def async_ring(self, doorbell_id: str, event_id: str | None = None) -> None:
        doorbell = self.doorbells_by_id.get(doorbell_id)
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
            "icon": DOORBELL_ICON,
        }
        enabled_targets = await self._dispatch(
            EVENT_TYPE_DOORBELL,
            doorbell_id,
            ctx,
            CONF_NOTIFY_TITLE,
            CONF_NOTIFY_MESSAGE,
            CONF_TTS_MESSAGE,
            self._frigate_notify_data(event_id, ctx),
        )

        # Always fire the event so automations can still react
        self.hass.bus.async_fire(
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

    # -- Frigate detection (person / car / ...) --------------------------------

    async def async_detect(
        self,
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
            "icon": LABEL_ICONS.get(label, DEFAULT_LABEL_ICON),
            "sub_label": sub_label or "",
            "event_id": event_id or "",
            "zones": ", ".join(zones),
        }

        enabled_targets = await self._dispatch(
            label,
            camera_id,
            ctx,
            CONF_DETECT_TITLE,
            CONF_DETECT_MESSAGE,
            CONF_DETECT_TTS_MESSAGE,
            self._frigate_notify_data(event_id, ctx),
        )

        self.hass.bus.async_fire(
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

    # -- MQTT ------------------------------------------------------------------

    def _make_doorbell_handler(self, doorbell: dict[str, Any]) -> Callable[[Any], None]:
        @callback
        def _on_doorbell_message(msg: Any) -> None:
            event_id = _event_id_from_payload(msg.payload)
            _LOGGER.debug(
                "MQTT ring for doorbell '%s' (event_id=%s)", doorbell[CONF_ID], event_id
            )
            self.hass.async_create_task(self.async_ring(doorbell[CONF_ID], event_id))

        return _on_doorbell_message

    def _remember(self, event_id: str) -> None:
        self._seen_events[event_id] = None
        while len(self._seen_events) > SEEN_EVENTS_MAX:
            self._seen_events.popitem(last=False)

    @callback
    def _on_frigate_message(self, msg: Any) -> None:
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
        if event_id in self._seen_events or after.get("false_positive"):
            return

        camera = self.cameras_by_frigate_name.get(after.get("camera"))
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

        self._remember(event_id)
        self.hass.async_create_task(
            self.async_detect(
                camera,
                label,
                event_id=event_id,
                sub_label=_normalise_sub_label(after.get("sub_label")),
                zones=zones,
            )
        )

    def _mqtt_subscriptions(self) -> list[tuple[str, Callable[[Any], None]]]:
        subs: list[tuple[str, Callable[[Any], None]]] = []
        for doorbell in self.doorbells:
            if doorbell.get(CONF_MQTT_TOPIC):
                subs.append(
                    (doorbell[CONF_MQTT_TOPIC], self._make_doorbell_handler(doorbell))
                )
        if self.cameras and self.frigate[CONF_MQTT]:
            subs.append((self.frigate[CONF_MQTT_TOPIC], self._on_frigate_message))
        return subs

    async def async_start(self) -> None:
        """Subscribe to doorbell topics and Frigate's event stream."""
        subscriptions = self._mqtt_subscriptions()
        if not subscriptions:
            return

        from homeassistant.components import mqtt  # pylint: disable=import-outside-toplevel

        topics = [topic for topic, _ in subscriptions]
        try:
            ready = await mqtt.async_wait_for_mqtt_client(self.hass)
        except Exception:  # pylint: disable=broad-except
            ready = False
        if not ready:
            _LOGGER.warning(
                "MQTT is not available; topics %s will not be handled. Use the "
                "dialmatrix.ring / dialmatrix.detect services from automations, "
                "or clear the MQTT options to silence this.",
                topics,
            )
            return

        for topic, handler in subscriptions:
            self._unsubscribe.append(
                await mqtt.async_subscribe(self.hass, topic, handler)
            )
        _LOGGER.info("Subscribed to MQTT topics %s", topics)

    async def async_stop(self) -> None:
        for unsub in self._unsubscribe:
            try:
                unsub()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.debug("MQTT unsubscribe failed", exc_info=True)
        self._unsubscribe.clear()


# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------


def _runtime(hass: HomeAssistant) -> DialMatrixRuntime | None:
    return hass.data.get(DOMAIN, {}).get("runtime")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the websocket API and import a legacy YAML block, if any."""
    hass.data.setdefault(DOMAIN, {})
    _async_register_websocket(hass)

    conf = config.get(DOMAIN)
    if conf is None:
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dial Matrix from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    conf = _upgrade_legacy_defaults(OPTIONS_SCHEMA(dict(entry.options)))
    runtime = DialMatrixRuntime(hass, conf)
    hass.data[DOMAIN]["runtime"] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    await runtime.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        runtime = hass.data[DOMAIN].pop("runtime", None)
        if runtime is not None:
            await runtime.async_stop()
        hass.services.async_remove(DOMAIN, "ring")
        hass.services.async_remove(DOMAIN, "detect")
    return ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "ring"):
        return

    async def handle_ring(call: ServiceCall) -> None:
        """Handle dialmatrix.ring — notify enabled targets and fire event."""
        runtime = _runtime(hass)
        if runtime is None:
            _LOGGER.error("dialmatrix.ring: integration is not set up")
            return
        await runtime.async_ring(call.data["doorbell_id"], call.data.get("event_id"))

    async def handle_detect(call: ServiceCall) -> None:
        """Handle dialmatrix.detect — notify enabled targets and fire event."""
        runtime = _runtime(hass)
        if runtime is None:
            _LOGGER.error("dialmatrix.detect: integration is not set up")
            return
        camera = runtime.cameras_by_id.get(call.data["camera_id"])
        if camera is None:
            _LOGGER.error(
                "dialmatrix.detect: unknown camera_id '%s'", call.data["camera_id"]
            )
            return
        await runtime.async_detect(
            camera,
            call.data["label"],
            event_id=call.data.get("event_id"),
            sub_label=call.data.get("sub_label"),
            zones=call.data.get("zones"),
        )

    hass.services.async_register(DOMAIN, "ring", handle_ring, schema=RING_SCHEMA)
    hass.services.async_register(DOMAIN, "detect", handle_detect, schema=DETECT_SCHEMA)


# -----------------------------------------------------------------------------
# Websocket API — used by the Dial Matrix card's inline editor
# -----------------------------------------------------------------------------


def _config_entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _defaults() -> dict[str, Any]:
    """Defaults the card uses when adding items."""
    return {
        "target": TARGET_SCHEMA({CONF_ID: "", CONF_NAME: ""}),
        "camera": CAMERA_SCHEMA({CONF_ID: "", CONF_NAME: ""}),
        "frigate": FRIGATE_SCHEMA({}),
        "labels": LABEL_OPTIONS,
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@callback
def ws_get_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return the current configuration (with defaults applied)."""
    entry = _config_entry(hass)
    options = _upgrade_legacy_defaults(OPTIONS_SCHEMA(dict(entry.options) if entry else {}))
    connection.send_result(
        msg["id"],
        {"config": options, "configured": entry is not None, "defaults": _defaults()},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/config/save", vol.Required("config"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Validate and store a full configuration; the entry reloads itself."""
    try:
        conf = OPTIONS_SCHEMA(msg["config"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_config", str(err))
        return

    entry = _config_entry(hass)
    if entry is None:
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
        )
    else:
        hass.config_entries.async_update_entry(entry, options=conf)
    connection.send_result(msg["id"], {"config": conf})


_TEST_TTS_DEFAULT_MESSAGE = "This is a test announcement from Dial Matrix"

# Sample values so placeholders in a message render sensibly during a test
_TEST_CTX = {
    "event_type": EVENT_TYPE_DOORBELL,
    "source_id": "test",
    "source_name": "Test",
    "doorbell_id": "test",
    "doorbell_name": "Front Door",
    "camera_id": "test",
    "camera_name": "Front Door",
    "label": "person",
    "label_title": "Person",
    "icon": DOORBELL_ICON,
    "sub_label": "",
    "event_id": "",
    "zones": "",
}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/tts/test",
        # Speaker settings as in a target (tts_entity, tts_media_player, …)
        vol.Required("target"): dict,
        vol.Optional("message", default=_TEST_TTS_DEFAULT_MESSAGE): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_test_tts(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Speak a test message with the given (possibly unsaved) speaker settings."""
    runtime = _runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_set_up", "Dial Matrix is not set up yet")
        return
    try:
        target = TARGET_SCHEMA({CONF_ID: "test", CONF_NAME: "Test", **msg["target"]})
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_target", str(err))
        return
    tts_entity = target.get(CONF_TTS_ENTITY)
    players = target.get(CONF_TTS_MEDIA_PLAYER) or []
    if not tts_entity or not players:
        connection.send_error(
            msg["id"], "invalid_target", "Pick a text-to-speech engine and at least one speaker"
        )
        return
    message = _render(msg["message"], _TEST_CTX)
    await runtime._speak(target, tts_entity, players, message)
    connection.send_result(msg["id"], {"players": players, "message": message})


@callback
def _async_register_websocket(hass: HomeAssistant) -> None:
    if hass.data[DOMAIN].get("ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_save_config)
    websocket_api.async_register_command(hass, ws_test_tts)
    hass.data[DOMAIN]["ws_registered"] = True
