"""Smoke tests for the Dial Matrix integration using stubbed HA modules."""
import asyncio, json, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.dirname(HERE))
import voluptuous as vol
from custom_components.dialmatrix import CONFIG_SCHEMA, OPTIONS_SCHEMA, async_setup, async_setup_entry, async_unload_entry, DOMAIN
from custom_components.dialmatrix.switch import async_setup_entry as switch_setup_entry
from custom_components.dialmatrix.config_flow import DialMatrixConfigFlow, DialMatrixOptionsFlow, default_options
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.core import ServiceCall
from homeassistant.components import mqtt
from homeassistant.helpers import issue_registry as ir
from homeassistant.components import websocket_api as ws

class Conn:
    def __init__(self): self.results = []; self.errors = []
    def send_result(self, id_, data): self.results.append(data)
    def send_error(self, id_, code, msg): self.errors.append((code, msg))

class Bus:
    def __init__(self): self.events = []
    def async_fire(self, name, data): self.events.append((name, data))

class Services:
    def __init__(self): self.handlers = {}; self.calls = []
    def async_register(self, domain, name, handler, schema=None): self.handlers[(domain, name)] = (handler, schema)
    def async_remove(self, domain, name): self.handlers.pop((domain, name), None)
    def has_service(self, domain, name): return (domain, name) in self.handlers
    async def async_call(self, domain, name, data, blocking=False): self.calls.append((f"{domain}.{name}", data))
    async def call(self, name, data):
        handler, schema = self.handlers[(DOMAIN, name)]
        await handler(ServiceCall(schema(data)))

class Flows:
    def __init__(self): self.inits = []
    async def async_init(self, domain, context=None, data=None): self.inits.append((domain, context, data))

class ConfigEntries:
    def __init__(self, hass): self.hass = hass; self.flow = Flows(); self.reloaded = []; self.forwarded = []; self.entries = []
    def async_entries(self, domain): return list(self.entries)
    def async_update_entry(self, entry, options=None):
        entry.options = options
        for fn in entry._listeners: self.hass.async_create_task(fn(self.hass, entry))
    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(platforms)
        await switch_setup_entry(self.hass, entry, lambda ents, update=False: None)
    async def async_unload_platforms(self, entry, platforms): return True
    async def async_reload(self, entry_id): self.reloaded.append(entry_id)

class States(dict):
    def get(self, entity_id, default=None):
        v = super().get(entity_id)
        return v if v is not None else default

class Hass:
    def __init__(self):
        self.data = {}; self.bus = Bus(); self.services = Services(); self.tasks = []
        self.config_entries = ConfigEntries(self)
        self.states = States({
            "media_player.living": types.SimpleNamespace(attributes={"supported_features": 1048576 | 4}),  # announce-capable (Sonos)
            "media_player.old": types.SimpleNamespace(attributes={"supported_features": 4}),
        })
    def async_create_task(self, coro): self.tasks.append(asyncio.ensure_future(coro))

YAML = {
    "doorbells": [{"id": "front", "name": "Front Door", "mqtt_topic": "home/doorbell/front/frigate_event"}],
    "cameras": [
        {"id": "driveway", "name": "Driveway", "zones": ["yard"]},
        {"id": "garden", "name": "Garden", "frigate_camera": "garden_cam", "labels": ["person"]},
        {"id": "doorbell", "name": "Doorbell", "zones": {"person": ["outside_driveway_person"], "car": ["outside_driveway_car"]}},
    ],
    "targets": [
        {"id": "alice", "name": "Alice", "notify_service": "notify.mobile_app_alice", "notify_data": {"push": {"sound": "x"}}},
        {"id": "speaker", "name": "Speaker", "tts_entity": "tts.google", "tts_media_player": "media_player.living"},
    ],
}

async def setup(options):
    hass = Hass(); entry = ConfigEntry(options=options)
    assert await async_setup_entry(hass, entry)
    await asyncio.gather(*hass.tasks); hass.tasks.clear()
    return hass, entry

async def main():
    # ---- YAML schema & import ------------------------------------------------
    conf = CONFIG_SCHEMA({DOMAIN: YAML})[DOMAIN]
    assert conf["cameras"][0]["labels"] == ["person", "car"]
    assert conf["cameras"][0]["zones"] == {"*": ["yard"]} and conf["cameras"][1]["zones"] == {}
    assert conf["cameras"][2]["zones"] == {"person": ["outside_driveway_person"], "car": ["outside_driveway_car"]}
    assert conf["frigate"]["mqtt"] is True and conf["frigate"]["mqtt_topic"] == "frigate/events"
    assert conf["targets"][1]["tts_media_player"] == ["media_player.living"] and conf["targets"][1]["tts_announce"] is True and "tts_volume" not in conf["targets"][1]
    assert OPTIONS_SCHEMA({"targets": [{"id": "s", "name": "S", "tts_media_player": ["media_player.a", "media_player.b"], "tts_volume": "35"}]})["targets"][0]["tts_volume"] == 35
    try: OPTIONS_SCHEMA({"targets": [{"id": "s", "name": "S", "tts_volume": 150}]}); assert False
    except vol.Invalid: pass
    assert OPTIONS_SCHEMA({}) == {"doorbells": [], "cameras": [], "targets": [], "frigate": conf["frigate"]}, "empty options ok"

    hass0 = Hass()
    assert await async_setup(hass0, {DOMAIN: conf})
    await asyncio.gather(*hass0.tasks)
    assert hass0.config_entries.flow.inits[0][1] == {"source": "import"} and ir.issues[-1][1] == "deprecated_yaml"
    hass0b = Hass(); assert await async_setup(hass0b, {}) and hass0b.config_entries.flow.inits == []
    assert CONFIG_SCHEMA({DOMAIN: None}) == {DOMAIN: None}, "empty dialmatrix: block tolerated"
    hass0c = Hass(); assert await async_setup(hass0c, {DOMAIN: None}) and hass0c.config_entries.flow.inits == []
    try:
        OPTIONS_SCHEMA({"targets": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}); assert False
    except vol.Invalid as err: assert "duplicate id" in str(err)
    # websocket API registered by async_setup
    assert set(ws.commands) == {"dialmatrix/config", "dialmatrix/config/save", "dialmatrix/tts/test"}
    conn = Conn(); await ws.commands["dialmatrix/tts/test"](hass0b, conn, {"id": 0, "type": "dialmatrix/tts/test", "target": {}, "message": "x"})
    assert conn.errors[0][0] == "not_set_up"
    conn = Conn(); ws.commands["dialmatrix/config"](hass0b, conn, {"id": 1, "type": "dialmatrix/config"})
    assert conn.results[0]["configured"] is False and conn.results[0]["config"]["targets"] == []
    assert conn.results[0]["defaults"]["target"]["notify_title"] == "$icon Doorbell" and conn.results[0]["defaults"]["camera"]["labels"] == ["person", "car"]
    conn = Conn(); await ws.commands["dialmatrix/config/save"](hass0b, conn, {"id": 2, "type": "dialmatrix/config/save", "config": {"doorbells": [{"id": "x"}]}})
    assert conn.errors and conn.errors[0][0] == "invalid_config"
    conn = Conn(); await ws.commands["dialmatrix/config/save"](hass0b, conn, {"id": 3, "type": "dialmatrix/config/save", "config": {"doorbells": [{"id": "x", "name": "X"}]}})
    assert conn.results[0]["config"]["doorbells"][0]["name"] == "X" and hass0b.config_entries.flow.inits[-1][1]["source"] == "import", "no entry → import flow"

    flow = DialMatrixConfigFlow()
    res = await flow.async_step_import(conf)
    assert res["type"] == "create_entry" and res["options"]["doorbells"] == conf["doorbells"] and res["options"]["frigate"]["mqtt_topic"] == "frigate/events"
    res = await flow.async_step_user({}); assert res["type"] == "create_entry" and res["options"] == default_options()
    ConfigFlow._entries = [object()]
    assert (await flow.async_step_user())["reason"] == "single_instance_allowed"
    assert (await flow.async_step_import(conf))["reason"] == "single_instance_allowed"
    ConfigFlow._entries = []

    # ---- runtime from a config entry -----------------------------------------
    hass, entry = await setup(conf)
    assert "frigate/events" in mqtt.subscriptions and "home/doorbell/front/frigate_event" in mqtt.subscriptions
    rt = hass.data[DOMAIN]["runtime"]; ents = rt.entities
    assert len(ents) == 12, len(ents)
    e = ents[("doorbell", "front", "alice")]
    assert e.slug == "dialmatrix_front_alice" and e.name == "Front Door → Alice"
    e2 = ents[("car", "driveway", "speaker")]
    assert e2.slug == "dialmatrix_driveway_car_speaker" and e2.name == "Driveway Car → Speaker"
    attrs = e2.extra_state_attributes
    assert attrs["event_type"] == "car" and attrs["source_id"] == "driveway" and attrs["camera_name"] == "Driveway" and attrs["sort_order"] == [2, 0, 1]
    assert ents[("doorbell", "front", "speaker")].extra_state_attributes["doorbell_id"] == "front"
    assert e.device_info["identifiers"] == {(DOMAIN, "entry1")}

    # Ring (icons via $icon in default titles)
    await hass.services.call("ring", {"doorbell_id": "front"})
    calls = hass.services.calls; hass.services.calls = []
    assert calls[0] == ("notify.mobile_app_alice", {"title": "🔔 Doorbell", "message": "Someone is at the Front Door door", "data": {"push": {"sound": "x"}}}), calls[0]
    assert calls[1] == ("media_player.play_media", {"entity_id": ["media_player.living"], "media_content_id": "media-source://tts/tts.google?message=Someone+is+at+the+Front+Door+door",
        "media_content_type": "music", "announce": True}), calls[1]
    assert hass.bus.events[-1] == ("dialmatrix_ring", {"doorbell_id": "front", "doorbell_name": "Front Door", "event_id": None, "enabled_targets": ["alice", "speaker"]})
    await hass.services.call("ring", {"doorbell_id": "front", "event_id": "ring1"})
    assert hass.services.calls[0][1]["data"] == {"tag": "dialmatrix_ring1", "image": "/api/frigate/notifications/ring1/thumbnail.jpg", "push": {"sound": "x"}}
    hass.services.calls = []

    # Detect via service, with speaker disabled for driveway/person
    await ents[("person", "driveway", "speaker")].async_turn_off()
    await hass.services.call("detect", {"camera_id": "driveway", "label": "person", "event_id": "123.4-abc", "zones": ["yard"]})
    calls = hass.services.calls; hass.services.calls = []
    assert calls == [("notify.mobile_app_alice", {"title": "🚶 Person detected", "message": "A person was detected at the Driveway",
        "data": {"tag": "dialmatrix_123.4-abc", "image": "/api/frigate/notifications/123.4-abc/thumbnail.jpg", "push": {"sound": "x"}}})], calls
    ev = hass.bus.events[-1]; assert ev[0] == "dialmatrix_detect" and ev[1]["enabled_targets"] == ["alice"] and ev[1]["zones"] == ["yard"]
    n = len(hass.bus.events)
    await hass.services.call("detect", {"camera_id": "garden", "label": "car"})
    await hass.services.call("detect", {"camera_id": "nope", "label": "car"})
    assert hass.services.calls == [] and len(hass.bus.events) == n

    # MQTT
    cb = mqtt.subscriptions["frigate/events"]
    def msg(type_, **after):
        cb(types.SimpleNamespace(topic="frigate/events", payload=json.dumps({"type": type_, "before": {}, "after": after})))
    async def flush():
        await asyncio.gather(*hass.tasks); hass.tasks.clear()
    msg("new", id="ev1", camera="driveway", label="car", entered_zones=[], current_zones=[])
    await flush(); assert hass.services.calls == [], "not in zone yet"
    msg("update", id="ev1", camera="driveway", label="car", entered_zones=["yard"], current_zones=["yard"], sub_label=["ABC-123", 0.9])
    await flush()
    assert len(hass.services.calls) == 2 and hass.services.calls[1][1]["media_content_id"].endswith("message=A+car+was+detected+at+the+Driveway")
    assert hass.services.calls[0][1]["title"] == "🚗 Car detected"
    assert hass.bus.events[-1][1]["sub_label"] == "ABC-123"; hass.services.calls = []
    msg("update", id="ev1", camera="driveway", label="car", entered_zones=["yard"], current_zones=["yard"])
    msg("end", id="ev1", camera="driveway", label="car", entered_zones=["yard"], current_zones=[])
    await flush(); assert hass.services.calls == [], "deduped"
    msg("new", id="ev2", camera="garden_cam", label="person")
    await flush(); assert len(hass.services.calls) == 2; hass.services.calls = []
    msg("new", id="ev3", camera="garden_cam", label="car"); msg("new", id="ev4", camera="unknown", label="person")
    msg("new", id="ev5", camera="garden_cam", label="person", false_positive=True)
    cb(types.SimpleNamespace(topic="frigate/events", payload="not json"))
    await flush(); assert hass.services.calls == []
    msg("update", id="d1", camera="doorbell", label="person", current_zones=["outside_driveway_car"])
    await flush(); assert hass.services.calls == [], "wrong zone for person"
    msg("update", id="d1", camera="doorbell", label="person", current_zones=["outside_driveway_person"])
    await flush(); assert len(hass.services.calls) == 2; hass.services.calls = []

    ring_cb = mqtt.subscriptions["home/doorbell/front/frigate_event"]
    ring_cb(types.SimpleNamespace(topic="x", payload="1700.1-abc")); await flush()
    assert hass.services.calls[0][1]["data"]["image"] == "/api/frigate/notifications/1700.1-abc/thumbnail.jpg"
    assert hass.bus.events[-1][1]["event_id"] == "1700.1-abc"; hass.services.calls = []
    ring_cb(types.SimpleNamespace(topic="x", payload='{"event_id": "j1"}')); await flush()
    assert hass.services.calls[0][1]["data"]["tag"] == "dialmatrix_j1"; hass.services.calls = []
    ring_cb(types.SimpleNamespace(topic="x", payload="")); await flush()
    assert "data" not in hass.services.calls[1][1] and hass.services.calls[0][1]["data"] == {"push": {"sound": "x"}}; hass.services.calls = []

    # Websocket save on a configured instance → entry updated + reload
    hass.config_entries.entries = [entry]
    conn = Conn(); ws.commands["dialmatrix/config"](hass, conn, {"id": 4, "type": "dialmatrix/config"})
    assert conn.results[0]["configured"] is True and [d["id"] for d in conn.results[0]["config"]["doorbells"]] == ["front"]
    new_conf = {**conf, "doorbells": conf["doorbells"] + [{"id": "back", "name": "Back"}]}
    conn = Conn(); await ws.commands["dialmatrix/config/save"](hass, conn, {"id": 5, "type": "dialmatrix/config/save", "config": new_conf})
    await asyncio.gather(*hass.tasks); hass.tasks.clear()
    assert [d["id"] for d in entry.options["doorbells"]] == ["front", "back"] and hass.config_entries.reloaded == ["entry1"]
    hass.config_entries.reloaded = []
    for fn in entry._listeners: await fn(hass, entry)
    assert hass.config_entries.reloaded == ["entry1"]
    mqtt.unsubscribed = []
    assert await async_unload_entry(hass, entry)
    assert "runtime" not in hass.data[DOMAIN] and not hass.services.has_service(DOMAIN, "ring")
    assert len(mqtt.unsubscribed) == 2

    # MQTT unavailable → warning only
    mqtt.available = False; mqtt.subscriptions.clear()
    hass2, _ = await setup(conf)
    assert mqtt.subscriptions == {} and hass2.services.has_service(DOMAIN, "detect")
    mqtt.available = True

    # frigate.mqtt false → doorbell topic only, image_url empty → no image
    hass3, _ = await setup({**conf, "frigate": {"mqtt": False, "image_url": ""}})
    assert list(mqtt.subscriptions) == ["home/doorbell/front/frigate_event"]
    await hass3.services.call("detect", {"camera_id": "driveway", "label": "car", "event_id": "z"})
    assert hass3.services.calls[0][1]["data"] == {"tag": "dialmatrix_z", "push": {"sound": "x"}}
    mqtt.subscriptions.clear()

    # TTS modes: volume + mixed players (announce-capable vs plain), announce off, unknown player
    tts_conf = {**conf, "targets": [
        {"id": "s1", "name": "S1", "tts_entity": "tts.google", "tts_media_player": ["media_player.living", "media_player.old"], "tts_volume": 40},
        {"id": "s2", "name": "S2", "tts_entity": "tts.google", "tts_media_player": "media_player.living", "tts_announce": False},
        {"id": "s3", "name": "S3", "tts_entity": "tts.google", "tts_media_player": "media_player.missing"},
    ]}
    hass7, _ = await setup(tts_conf)
    await hass7.services.call("ring", {"doorbell_id": "front"})
    c = hass7.services.calls
    assert c[0] == ("media_player.play_media", {"entity_id": ["media_player.living"], "media_content_id": "media-source://tts/tts.google?message=Someone+is+at+the+Front+Door+door",
        "media_content_type": "music", "announce": True, "extra": {"volume": 40}}), c[0]
    assert c[1] == ("tts.speak", {"entity_id": "tts.google", "media_player_entity_id": ["media_player.old"], "message": "Someone is at the Front Door door"}), c[1]
    assert c[2] == ("tts.speak", {"entity_id": "tts.google", "media_player_entity_id": ["media_player.living"], "message": "Someone is at the Front Door door"}), "announce disabled → plain"
    assert c[3][0] == "tts.speak" and c[3][1]["media_player_entity_id"] == ["media_player.missing"], "unknown player → plain"
    assert len(c) == 4
    # TTS test over websocket: unsaved target settings, placeholders rendered, same speak path
    hass7.services.calls = []
    conn = Conn(); await ws.commands["dialmatrix/tts/test"](hass7, conn, {"id": 1, "type": "dialmatrix/tts/test",
        "target": {"tts_entity": "tts.google", "tts_media_player": ["media_player.living"], "tts_volume": 25}, "message": "$icon Test at the $doorbell_name"})
    assert conn.results[0] == {"players": ["media_player.living"], "message": "🔔 Test at the Front Door"}, conn.results
    assert hass7.services.calls == [("media_player.play_media", {"entity_id": ["media_player.living"], "media_content_id": "media-source://tts/tts.google?message=%F0%9F%94%94+Test+at+the+Front+Door",
        "media_content_type": "music", "announce": True, "extra": {"volume": 25}})], hass7.services.calls
    conn = Conn(); await ws.commands["dialmatrix/tts/test"](hass7, conn, {"id": 2, "type": "dialmatrix/tts/test", "target": {"tts_entity": "tts.google"}, "message": "x"})
    assert conn.errors[0][0] == "invalid_target"
    conn = Conn(); await ws.commands["dialmatrix/tts/test"](hass7, conn, {"id": 3, "type": "dialmatrix/tts/test", "target": {"tts_entity": "tts.google", "tts_media_player": ["media_player.old"], "tts_volume": 500}, "message": "x"})
    assert conn.errors[0][0] == "invalid_target"
    mqtt.subscriptions.clear()

    # Entry stored before $icon existed: old literal defaults upgraded, custom texts kept
    legacy = {**conf, "targets": [
        {"id": "a", "name": "A", "notify_service": "notify.a", "notify_title": "Doorbell", "detect_title": "$label_title detected", "detect_message": "$label_title detected at $camera_name"},
        {"id": "b", "name": "B", "notify_service": "notify.b", "notify_title": "Ring!", "detect_title": "Look"},
    ]}
    hass6, entry6 = await setup(legacy)
    await hass6.services.call("ring", {"doorbell_id": "front"})
    assert hass6.services.calls[0][1]["title"] == "🔔 Doorbell" and hass6.services.calls[1][1]["title"] == "Ring!", hass6.services.calls
    hass6.services.calls = []
    await hass6.services.call("detect", {"camera_id": "driveway", "label": "car"})
    assert hass6.services.calls[0][1] == {"title": "🚗 Car detected", "message": "A car was detected at the Driveway"} and hass6.services.calls[1][1]["title"] == "Look"
    hass6.config_entries.entries = [entry6]
    conn = Conn(); ws.commands["dialmatrix/config"](hass6, conn, {"id": 9, "type": "dialmatrix/config"})
    assert conn.results[0]["config"]["targets"][0]["notify_title"] == "$icon Doorbell", "editor shows upgraded default"
    mqtt.subscriptions.clear()

    # Empty entry (fresh UI install) → no entities, no subscriptions, no crash
    hass4, _ = await setup({})
    assert hass4.data[DOMAIN]["runtime"].entities == {} and mqtt.subscriptions == {}

    # ---- options flow --------------------------------------------------------
    of = DialMatrixOptionsFlow(ConfigEntry(options={}))
    r = await of.async_step_init(); assert r["type"] == "menu" and r["menu_options"] == ["doorbells", "cameras", "targets", "frigate", "save"]
    r = await of.async_step_doorbells(); assert r["menu_options"] == ["add_doorbell", "init"], r
    r = await of.async_step_add_doorbell(); assert r["type"] == "form" and r["step_id"] == "doorbell_form"
    r["data_schema"]({"id": "front", "name": "Front", "mqtt_topic": "home/doorbell/front/frigate_event"})
    r = await of.async_step_doorbell_form({"id": "front", "name": "Front", "mqtt_topic": "home/doorbell/front/frigate_event"})
    assert r["type"] == "menu" and r["step_id"] == "doorbells" and r["menu_options"] == ["add_doorbell", "edit_doorbell", "remove_doorbell", "init"]
    r = await of.async_step_add_doorbell(); r = await of.async_step_doorbell_form({"id": "front", "name": "Dup"})
    assert r["type"] == "form" and r["errors"] == {"id": "duplicate_id"}
    r = await of.async_step_doorbell_form({"id": "back", "name": "Back"}); assert len(of._options["doorbells"]) == 2
    r = await of.async_step_edit_doorbell(); assert r["type"] == "form" and r["step_id"] == "edit_doorbell"
    r = await of.async_step_edit_doorbell({"item": "back"}); assert r["step_id"] == "doorbell_form"
    r = await of.async_step_doorbell_form({"id": "back", "name": "Back Door"})
    assert of._options["doorbells"][1] == {"id": "back", "name": "Back Door"} and len(of._options["doorbells"]) == 2
    r = await of.async_step_remove_doorbell({"item": ["back"]}); assert [d["id"] for d in of._options["doorbells"]] == ["front"]
    r = await of.async_step_add_camera(); assert r["step_id"] == "camera_form"
    r = await of.async_step_camera_form({"id": "doorbell", "name": "Doorbell", "labels": []}); assert r["errors"] == {"labels": "no_labels"}
    r = await of.async_step_camera_form({"id": "doorbell", "name": "Doorbell", "labels": ["person", "car"]})
    assert r["type"] == "form" and r["step_id"] == "camera_zones"
    r["data_schema"]({"person": "outside_driveway_person"})
    r = await of.async_step_camera_zones({"person": "outside_driveway_person, yard", "car": ""})
    assert r["step_id"] == "cameras"
    assert of._options["cameras"] == [{"id": "doorbell", "name": "Doorbell", "labels": ["person", "car"], "zones": {"person": ["outside_driveway_person", "yard"]}}], of._options["cameras"]
    r = await of.async_step_add_target(); assert r["step_id"] == "target_form"
    r = await of.async_step_target_form({"id": "nik", "name": "Nik", "notify_service": "notify.mobile_app_nik", "notify_title": "Ring", "notify_message": "$doorbell_name",
        "detect_title": "$label_title", "detect_message": "x", "notify_data": "oops", "tts_message": "a", "detect_tts_message": "b"})
    assert r["errors"] == {"notify_data": "notify_data_not_mapping"}
    r = await of.async_step_target_form({"id": "nik", "name": "Nik", "notify_service": "notify.mobile_app_nik", "notify_title": "Ring", "notify_message": "$doorbell_name",
        "detect_title": "$label_title", "detect_message": "x", "notify_data": {"url": "/gate"}, "tts_message": "a", "detect_tts_message": "b"})
    assert r["step_id"] == "targets" and of._options["targets"][0]["notify_data"] == {"url": "/gate"}
    r = await of.async_step_frigate(); assert r["type"] == "form"
    r = await of.async_step_frigate({"mqtt": True, "mqtt_topic": "frigate/events", "image_url": "/api/frigate/notifications/$event_id/snapshot.jpg"})
    assert r["step_id"] == "init"
    r = await of.async_step_save(); assert r["type"] == "create_entry"
    saved = r["data"]
    hass5, _ = await setup(saved)
    rt5 = hass5.data[DOMAIN]["runtime"]
    assert len(rt5.entities) == 3 and rt5.frigate["image_url"].endswith("snapshot.jpg")
    assert set(mqtt.subscriptions) == {"frigate/events", "home/doorbell/front/frigate_event"}
    of2 = DialMatrixOptionsFlow(ConfigEntry(options=saved))
    await of2.async_step_cameras(); await of2.async_step_edit_camera({"item": "doorbell"})
    assert of2._current()["zones"] == {"person": ["outside_driveway_person", "yard"]}
    print("BACKEND_TESTS_OK")

asyncio.run(main())
