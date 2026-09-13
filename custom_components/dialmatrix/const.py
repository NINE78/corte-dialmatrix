"""Constants for Dial Matrix."""
from __future__ import annotations

DOMAIN = "dialmatrix"

# Event types (row kinds in the matrix)
EVENT_TYPE_DOORBELL = "doorbell"
DEFAULT_LABELS = ["person", "car"]
LABEL_OPTIONS = [
    "person",
    "car",
    "dog",
    "cat",
    "bird",
    "bicycle",
    "motorcycle",
    "bus",
    "truck",
]

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

DEFAULT_NOTIFY_TITLE = "Doorbell"
DEFAULT_NOTIFY_MESSAGE = "Someone is at the $doorbell_name door"
DEFAULT_TTS_MESSAGE = "Someone is at the $doorbell_name door"
DEFAULT_DETECT_TITLE = "$label_title detected"
DEFAULT_DETECT_MESSAGE = "$label_title detected at $camera_name"
DEFAULT_DETECT_TTS_MESSAGE = "A $label was detected at the $camera_name"
DEFAULT_FRIGATE_TOPIC = "frigate/events"
DEFAULT_FRIGATE_IMAGE_URL = "/api/frigate/notifications/$event_id/thumbnail.jpg"

# Bound on remembered Frigate event ids (dedup of MQTT new/update messages)
SEEN_EVENTS_MAX = 1000
