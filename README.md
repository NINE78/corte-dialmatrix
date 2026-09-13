# Dial Matrix

A Home Assistant custom integration that implements a visual **event routing matrix**: choose which notification targets (phones, speakers) are alerted for each doorbell ring and each [Frigate](https://frigate.video) object detection (person, car, …).

## What it does

- Creates a `switch` entity for every **source × notification target** pair, where a source is
  - a **doorbell**, or
  - a **camera × label** (e.g. _Driveway · person_, _Driveway · car_)
- When a doorbell rings (MQTT topic per doorbell, or the `dialmatrix.ring` service), the integration reads the enabled switches and fans out notifications automatically
- Subscribes to Frigate's MQTT event stream (`frigate/events`) and does the same for person / car detections. Push notifications include the Frigate snapshot.
- No automations required: point each doorbell at its MQTT topic and the integration handles rings and detections end to end
- Fires `dialmatrix_ring` and `dialmatrix_detect` events so automations can react as well
- A companion [Lovelace card](https://github.com/NINE78/corte-dialmatrix-card) provides a visual grid to toggle routing cells on/off

## Installation

1. Install via [HACS](https://hacs.xyz) by adding this repository as a custom integration repository, then restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration** and pick **Dial Matrix**.
3. Open the integration's **Configure** dialog to add doorbells, cameras and notification targets. Pick **Save and apply** when done; the switch entities are created immediately.

## Configuration

Everything is managed from the **Configure** dialog (options flow):

- **Doorbells** — id, name and an optional MQTT topic that rings it.
- **Cameras (Frigate)** — id, name, optional Frigate camera name, the labels that get a row (person, car, …) and, per label, the zones an object must enter before anyone is notified.
- **Notification targets** — a phone (notify service) and/or a speaker (TTS entity + media player), with the push and TTS texts for doorbell rings and detections.
- **Frigate settings** — whether to listen on Frigate's MQTT topic, the topic name, and the image URL attached to pushes.

The card then shows one row per doorbell and per camera × label, one column per target, and every cell defaults to **on**.

### Migrating from configuration.yaml

A legacy `dialmatrix:` block is imported into the integration automatically on the first start after upgrading, and a repair issue reminds you to delete it. The YAML is ignored afterwards. The equivalent YAML for reference:

```yaml
dialmatrix:
  doorbells:
    - id: front
      name: 'Front Door'
      mqtt_topic: home/doorbell/front # optional; payload = Frigate event id
    - id: garden
      name: 'Garden Gate'
  cameras:
    - id: driveway
      name: 'Driveway'
      # labels: [person, car]   # default
    - id: backyard
      name: 'Backyard'
      frigate_camera: back_cam # Frigate camera name, if different from id
      labels: [person]
      zones: [patio] # only notify once the object enters one of these zones
    - id: doorbell
      name: 'Doorbell'
      zones: # zones can also be given per label
        person: [outside_driveway_person]
        car: [outside_driveway_car]
  targets:
    - id: alice_phone
      name: 'Alice'
      notify_service: notify.mobile_app_alice_iphone
    - id: bob_phone
      name: 'Bob'
      notify_service: notify.mobile_app_bob_pixel
      notify_message: 'Doorbell at the $doorbell_name!'
      detect_message: '$label_title spotted at the $camera_name'
    - id: living_room
      name: 'Living Room Speaker'
      tts_entity: tts.google_en_com
      tts_media_player: media_player.living_room_speaker
  frigate: # optional
    mqtt: true # subscribe to Frigate events over MQTT (default: true)
    mqtt_topic: frigate/events
    image_url: /api/frigate/notifications/$event_id/thumbnail.jpg
```

The option tables below use the YAML key names; the Configure dialog shows the same fields with friendly labels.

### Doorbell options

| Key          | Required | Description                                                                                                                     |
| ------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `id`         | ✅       | Unique identifier used internally                                                                                               |
| `name`       | ✅       | Display name shown in the Lovelace card                                                                                         |
| `mqtt_topic` |          | MQTT topic that rings this doorbell. The payload is used as Frigate event id (bare string, or JSON with `event_id` / `id`) to attach a snapshot |

### Camera options

| Key              | Required | Description                                                                                     |
| ---------------- | -------- | ----------------------------------------------------------------------------------------------- |
| `id`             | ✅       | Unique identifier used internally                                                               |
| `name`           | ✅       | Display name shown in the Lovelace card                                                         |
| `frigate_camera` |          | Camera name as Frigate reports it in MQTT payloads. Default: same as `id`                       |
| `labels`         |          | Object labels that get their own matrix row. Default: `[person, car]`                           |
| `zones`          |          | Frigate zone names, either a list (all labels) or a mapping `label: [zones]`. When set, an event only dispatches once the object has entered one of them |

### Target options

| Key                  | Required | Description                                                                                              |
| -------------------- | -------- | -------------------------------------------------------------------------------------------------------- |
| `id`                 | ✅       | Unique identifier used internally                                                                        |
| `name`               | ✅       | Display name shown in the Lovelace card                                                                  |
| `notify_service`     |          | HA notify service, e.g. `notify.mobile_app_foo`                                                          |
| `notify_title`       |          | Push title for doorbell rings. Default: `Doorbell`                                                       |
| `notify_message`     |          | Push body for doorbell rings. Default: `Someone is at the $doorbell_name door`                           |
| `detect_title`       |          | Push title for detections. Default: `$label_title detected`                                              |
| `detect_message`     |          | Push body for detections. Default: `$label_title detected at $camera_name`                               |
| `notify_data`        |          | Extra `data:` merged into every push for this target (e.g. `push: { sound: ... }`, `channel`, `actions`) |
| `tts_entity`         |          | TTS entity, e.g. `tts.google_en_com`                                                                     |
| `tts_media_player`   |          | Target media player entity ID                                                                            |
| `tts_message`        |          | TTS text for doorbell rings. Default: `Someone is at the $doorbell_name door`                            |
| `detect_tts_message` |          | TTS text for detections. Default: `A $label was detected at the $camera_name`                            |

### Frigate options

| Key          | Default                                              | Description                                                                                                                                       |
| ------------ | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mqtt`       | `true`                                               | Subscribe to Frigate's MQTT event topic and dispatch detections automatically. Requires the MQTT integration                                      |
| `mqtt_topic` | `frigate/events`                                     | Topic to subscribe to                                                                                                                             |
| `image_url`  | `/api/frigate/notifications/$event_id/thumbnail.jpg` | Image attached to push notifications (served by the Frigate HA integration). Set to `''` to disable. Use `.../snapshot.jpg` for the full snapshot |

### Message placeholders

Messages use `$name` / `${name}` placeholders:

| Placeholder                                 | Available for | Description                                                 |
| ------------------------------------------- | ------------- | ----------------------------------------------------------- |
| `$doorbell_name`, `$doorbell_id`            | doorbell      | The doorbell that rang                                      |
| `$camera_name`, `$camera_id`                | detection     | The camera that detected the object                         |
| `$label`, `$label_title`                    | detection     | Object label, lower-case (`car`) and capitalised (`Car`)    |
| `$sub_label`                                | detection     | Frigate sub label (recognised face / licence plate), if any |
| `$zones`                                    | detection     | Comma-separated zones the object is in                      |
| `$event_id`                                 | both          | Frigate event id, when supplied                             |
| `$source_name`, `$source_id`, `$event_type` | both          | Generic equivalents                                         |

## Automations

None are needed when every doorbell has an `mqtt_topic` and `frigate.mqtt` is on (the default). The services below exist for custom pipelines.

### Doorbells

Without `mqtt_topic`, trigger the ring service yourself:

```yaml
automation:
  - alias: 'Doorbell — Front Door'
    trigger:
      - platform: mqtt
        topic: doorbell/front
    action:
      - service: dialmatrix.ring
        data:
          doorbell_id: front
          # optional: a Frigate event id attaches its snapshot to the push
          # event_id: '{{ trigger.payload }}'
```

### Frigate detections

With `frigate.mqtt: true` (the default) nothing else is needed: the integration listens on `frigate/events`, matches the camera and label against your `cameras:` config, honours `zones`, ignores false positives, and notifies once per Frigate event.

If you prefer to drive it yourself (custom pipeline, `frigate.mqtt: false`), call `dialmatrix.detect`:

```yaml
automation:
  - alias: 'Driveway person'
    trigger:
      - platform: mqtt
        topic: frigate/events
    condition:
      - '{{ trigger.payload_json.type == "new" }}'
      - '{{ trigger.payload_json.after.camera == "driveway" }}'
      - '{{ trigger.payload_json.after.label == "person" }}'
    action:
      - service: dialmatrix.detect
        data:
          camera_id: driveway
          label: '{{ trigger.payload_json.after.label }}'
          event_id: '{{ trigger.payload_json.after.id }}'
          zones: '{{ trigger.payload_json.after.entered_zones }}'
```

## Entities

| Source           | Entity id                                     | Example                                      |
| ---------------- | --------------------------------------------- | -------------------------------------------- |
| Doorbell         | `switch.dialmatrix_{doorbell}_{target}`       | `switch.dialmatrix_front_alice_phone`        |
| Camera detection | `switch.dialmatrix_{camera}_{label}_{target}` | `switch.dialmatrix_driveway_car_alice_phone` |

Every switch exposes `event_type`, `source_id`, `source_name`, `target_id`, `target_name` and `sort_order` attributes (plus `doorbell_*` or `camera_*` / `label` depending on the source), which the card uses to build the grid. Switches default to **on** and restore their state across restarts.

## Events

Every ring fires a `dialmatrix_ring` event on the HA event bus:

```json
{
  "doorbell_id": "front",
  "doorbell_name": "Front Door",
  "event_id": null,
  "enabled_targets": ["alice_phone", "living_room"]
}
```

Every detection fires a `dialmatrix_detect` event:

```json
{
  "camera_id": "driveway",
  "camera_name": "Driveway",
  "label": "car",
  "sub_label": null,
  "event_id": "1700000000.123456-abcdef",
  "zones": ["yard"],
  "enabled_targets": ["alice_phone"]
}
```

## Lovelace card

Install the companion [Dial Matrix Card](https://github.com/NINE78/corte-dialmatrix-card) from HACS, then add to your dashboard:

```yaml
type: custom:dialmatrix-card
title: 'Call Routing Matrix'
```
