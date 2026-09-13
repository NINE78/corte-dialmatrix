"""Config and options flows for Dial Matrix.

The config flow only creates the (single) entry. Everything — doorbells,
cameras, targets and Frigate settings — is managed in the options flow, which
is a small menu-driven editor over the lists stored in `entry.options`.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

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
    CONF_TTS_ENTITY,
    CONF_TTS_MEDIA_PLAYER,
    CONF_TTS_MESSAGE,
    CONF_ZONES,
    DEFAULT_DETECT_MESSAGE,
    DEFAULT_DETECT_TITLE,
    DEFAULT_DETECT_TTS_MESSAGE,
    DEFAULT_FRIGATE_IMAGE_URL,
    DEFAULT_FRIGATE_TOPIC,
    DEFAULT_LABELS,
    DEFAULT_NOTIFY_MESSAGE,
    DEFAULT_NOTIFY_TITLE,
    DEFAULT_TTS_MESSAGE,
    DOMAIN,
    LABEL_OPTIONS,
)

TITLE = "Dial Matrix"

# The three editable lists and the field the options form uses to pick an item
_KINDS = (CONF_DOORBELLS, CONF_CAMERAS, CONF_TARGETS)
_SELECT = "item"


def default_options() -> dict[str, Any]:
    return {
        CONF_DOORBELLS: [],
        CONF_CAMERAS: [],
        CONF_TARGETS: [],
        CONF_FRIGATE: {
            CONF_MQTT: True,
            CONF_MQTT_TOPIC: DEFAULT_FRIGATE_TOPIC,
            CONF_IMAGE_URL: DEFAULT_FRIGATE_IMAGE_URL,
        },
    }


def _text(multiline: bool = False) -> selector.TextSelector:
    return selector.TextSelector(
        selector.TextSelectorConfig(
            multiline=multiline, type=selector.TextSelectorType.TEXT
        )
    )


def _doorbell_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ID): _text(),
            vol.Required(CONF_NAME): _text(),
            vol.Optional(CONF_MQTT_TOPIC): _text(),
        }
    )


def _camera_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ID): _text(),
            vol.Required(CONF_NAME): _text(),
            vol.Optional(CONF_FRIGATE_CAMERA): _text(),
            vol.Required(CONF_LABELS, default=DEFAULT_LABELS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=LABEL_OPTIONS, multiple=True, custom_value=True
                )
            ),
        }
    )


def _camera_zones_schema(labels: list[str]) -> vol.Schema:
    return vol.Schema({vol.Optional(label): _text() for label in labels})


def _target_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ID): _text(),
            vol.Required(CONF_NAME): _text(),
            vol.Optional(CONF_NOTIFY_SERVICE): _text(),
            vol.Required(CONF_NOTIFY_TITLE, default=DEFAULT_NOTIFY_TITLE): _text(),
            vol.Required(CONF_NOTIFY_MESSAGE, default=DEFAULT_NOTIFY_MESSAGE): _text(),
            vol.Required(CONF_DETECT_TITLE, default=DEFAULT_DETECT_TITLE): _text(),
            vol.Required(CONF_DETECT_MESSAGE, default=DEFAULT_DETECT_MESSAGE): _text(),
            vol.Optional(CONF_NOTIFY_DATA): selector.ObjectSelector(),
            vol.Optional(CONF_TTS_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="tts")
            ),
            vol.Optional(CONF_TTS_MEDIA_PLAYER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            vol.Required(CONF_TTS_MESSAGE, default=DEFAULT_TTS_MESSAGE): _text(),
            vol.Required(
                CONF_DETECT_TTS_MESSAGE, default=DEFAULT_DETECT_TTS_MESSAGE
            ): _text(),
        }
    )


def _frigate_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MQTT, default=True): selector.BooleanSelector(),
            vol.Required(CONF_MQTT_TOPIC, default=DEFAULT_FRIGATE_TOPIC): _text(),
            vol.Optional(CONF_IMAGE_URL, default=DEFAULT_FRIGATE_IMAGE_URL): _text(),
        }
    )


def _pick_schema(items: list[dict[str, Any]], multiple: bool = False) -> vol.Schema:
    options = [
        selector.SelectOptionDict(value=item[CONF_ID], label=f"{item[CONF_NAME]} ({item[CONF_ID]})")
        for item in items
    ]
    return vol.Schema(
        {
            vol.Required(_SELECT): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=multiple)
            )
        }
    )


def _zones_to_form(zones: Any, labels: list[str]) -> dict[str, str]:
    """Stored zones (list or mapping) → one comma-separated string per label."""
    if isinstance(zones, list):
        zones = {"*": zones}
    zones = zones or {}
    out: dict[str, str] = {}
    for label in labels:
        value = zones.get(label) or zones.get("*") or []
        if value:
            out[label] = ", ".join(value)
    return out


def _zones_from_form(user_input: dict[str, Any], labels: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for label in labels:
        raw = str(user_input.get(label) or "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            out[label] = parts
    return out


class DialMatrixConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single Dial Matrix entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={}, options=default_options())
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    async def async_step_import(self, import_data: dict[str, Any]):
        """Import a legacy `dialmatrix:` YAML block."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        options = {**default_options(), **import_data}
        options[CONF_FRIGATE] = {**default_options()[CONF_FRIGATE], **(import_data.get(CONF_FRIGATE) or {})}
        return self.async_create_entry(title=TITLE, data={}, options=options)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DialMatrixOptionsFlow:
        return DialMatrixOptionsFlow(config_entry)


class DialMatrixOptionsFlow(OptionsFlow):
    """Menu-driven editor for doorbells, cameras, targets and Frigate settings."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._options: dict[str, Any] = {
            **default_options(),
            **{k: list(v) for k, v in entry.options.items() if k in _KINDS},
            CONF_FRIGATE: {**default_options()[CONF_FRIGATE], **(entry.options.get(CONF_FRIGATE) or {})},
        }
        self._kind: str | None = None  # list currently being edited
        self._index: int | None = None  # index within that list (None = add)
        self._draft: dict[str, Any] = {}  # partially built item (camera zones step)

    # -- helpers ---------------------------------------------------------------

    def _items(self) -> list[dict[str, Any]]:
        assert self._kind is not None
        return self._options[self._kind]

    def _current(self) -> dict[str, Any]:
        return self._items()[self._index] if self._index is not None else {}

    def _duplicate_id(self, item_id: str) -> bool:
        return any(
            item[CONF_ID] == item_id
            for idx, item in enumerate(self._items())
            if idx != self._index
        )

    def _store(self, item: dict[str, Any]) -> None:
        if self._index is None:
            self._items().append(item)
        else:
            self._items()[self._index] = item
        self._index = None
        self._draft = {}

    def _index_of(self, item_id: str) -> int | None:
        for idx, item in enumerate(self._items()):
            if item[CONF_ID] == item_id:
                return idx
        return None

    def _menu_for_kind(self):
        return getattr(self, f"async_step_{self._kind}")()

    async def _list_menu(self, kind: str):
        self._kind = kind
        self._index = None
        self._draft = {}
        singular = kind[:-1]
        options = [f"add_{singular}"]
        if self._items():
            options += [f"edit_{singular}", f"remove_{singular}"]
        options.append("init")
        return self.async_show_menu(step_id=kind, menu_options=options)

    async def _pick(self, step_id: str, user_input: dict[str, Any] | None, multiple: bool):
        """Show a picker over the current list; returns (result, chosen) tuple."""
        if user_input is None:
            return self.async_show_form(step_id=step_id, data_schema=_pick_schema(self._items(), multiple)), None
        return None, user_input[_SELECT]

    async def _remove(self, step_id: str, user_input: dict[str, Any] | None):
        result, chosen = await self._pick(step_id, user_input, multiple=True)
        if result is not None:
            return result
        chosen_ids = set(chosen if isinstance(chosen, list) else [chosen])
        self._options[self._kind] = [i for i in self._items() if i[CONF_ID] not in chosen_ids]
        return await self._menu_for_kind()

    async def _begin_edit(self, step_id: str, user_input: dict[str, Any] | None, form_step):
        result, chosen = await self._pick(step_id, user_input, multiple=False)
        if result is not None:
            return result
        self._index = self._index_of(chosen)
        return await form_step()

    # -- main menu -------------------------------------------------------------

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[CONF_DOORBELLS, CONF_CAMERAS, CONF_TARGETS, CONF_FRIGATE, "save"],
        )

    async def async_step_save(self, user_input: dict[str, Any] | None = None):
        return self.async_create_entry(title="", data=self._options)

    # -- doorbells -------------------------------------------------------------

    async def async_step_doorbells(self, user_input=None):
        return await self._list_menu(CONF_DOORBELLS)

    async def async_step_add_doorbell(self, user_input=None):
        self._kind = CONF_DOORBELLS
        self._index = None
        return await self.async_step_doorbell_form(user_input)

    async def async_step_edit_doorbell(self, user_input=None):
        self._kind = CONF_DOORBELLS
        return await self._begin_edit("edit_doorbell", user_input, self.async_step_doorbell_form)

    async def async_step_remove_doorbell(self, user_input=None):
        self._kind = CONF_DOORBELLS
        return await self._remove("remove_doorbell", user_input)

    async def async_step_doorbell_form(self, user_input=None):
        self._kind = CONF_DOORBELLS
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._duplicate_id(user_input[CONF_ID]):
                errors[CONF_ID] = "duplicate_id"
            else:
                self._store(dict(user_input))
                return await self.async_step_doorbells()
        return self.async_show_form(
            step_id="doorbell_form",
            data_schema=self.add_suggested_values_to_schema(
                _doorbell_schema(), user_input or self._current()
            ),
            errors=errors,
        )

    # -- cameras ---------------------------------------------------------------

    async def async_step_cameras(self, user_input=None):
        return await self._list_menu(CONF_CAMERAS)

    async def async_step_add_camera(self, user_input=None):
        self._kind = CONF_CAMERAS
        self._index = None
        return await self.async_step_camera_form(user_input)

    async def async_step_edit_camera(self, user_input=None):
        self._kind = CONF_CAMERAS
        return await self._begin_edit("edit_camera", user_input, self.async_step_camera_form)

    async def async_step_remove_camera(self, user_input=None):
        self._kind = CONF_CAMERAS
        return await self._remove("remove_camera", user_input)

    async def async_step_camera_form(self, user_input=None):
        self._kind = CONF_CAMERAS
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._duplicate_id(user_input[CONF_ID]):
                errors[CONF_ID] = "duplicate_id"
            elif not user_input.get(CONF_LABELS):
                errors[CONF_LABELS] = "no_labels"
            else:
                self._draft = dict(user_input)
                return await self.async_step_camera_zones()
        return self.async_show_form(
            step_id="camera_form",
            data_schema=self.add_suggested_values_to_schema(
                _camera_schema(), user_input or self._current()
            ),
            errors=errors,
        )

    async def async_step_camera_zones(self, user_input=None):
        self._kind = CONF_CAMERAS
        labels: list[str] = list(self._draft.get(CONF_LABELS) or [])
        if user_input is not None:
            item = {**self._draft, CONF_ZONES: _zones_from_form(user_input, labels)}
            self._store(item)
            return await self.async_step_cameras()
        return self.async_show_form(
            step_id="camera_zones",
            data_schema=self.add_suggested_values_to_schema(
                _camera_zones_schema(labels),
                _zones_to_form(self._current().get(CONF_ZONES), labels),
            ),
            description_placeholders={"camera": self._draft.get(CONF_NAME, "")},
        )

    # -- targets ---------------------------------------------------------------

    async def async_step_targets(self, user_input=None):
        return await self._list_menu(CONF_TARGETS)

    async def async_step_add_target(self, user_input=None):
        self._kind = CONF_TARGETS
        self._index = None
        return await self.async_step_target_form(user_input)

    async def async_step_edit_target(self, user_input=None):
        self._kind = CONF_TARGETS
        return await self._begin_edit("edit_target", user_input, self.async_step_target_form)

    async def async_step_remove_target(self, user_input=None):
        self._kind = CONF_TARGETS
        return await self._remove("remove_target", user_input)

    async def async_step_target_form(self, user_input=None):
        self._kind = CONF_TARGETS
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._duplicate_id(user_input[CONF_ID]):
                errors[CONF_ID] = "duplicate_id"
            elif not isinstance(user_input.get(CONF_NOTIFY_DATA, {}), dict):
                errors[CONF_NOTIFY_DATA] = "notify_data_not_mapping"
            else:
                self._store(dict(user_input))
                return await self.async_step_targets()
        return self.async_show_form(
            step_id="target_form",
            data_schema=self.add_suggested_values_to_schema(
                _target_schema(), user_input or self._current()
            ),
            errors=errors,
        )

    # -- frigate ---------------------------------------------------------------

    async def async_step_frigate(self, user_input=None):
        if user_input is not None:
            self._options[CONF_FRIGATE] = {
                CONF_MQTT: user_input[CONF_MQTT],
                CONF_MQTT_TOPIC: user_input[CONF_MQTT_TOPIC],
                CONF_IMAGE_URL: user_input.get(CONF_IMAGE_URL, ""),
            }
            return await self.async_step_init()
        return self.async_show_form(
            step_id="frigate",
            data_schema=self.add_suggested_values_to_schema(
                _frigate_schema(), self._options[CONF_FRIGATE]
            ),
        )
