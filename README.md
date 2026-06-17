# Dial Matrix

A Home Assistant custom integration that implements a visual **call routing matrix** for doorbell buttons and notification targets.

## What it does

- Creates a `switch` entity for every **doorbell × notification target** pair
- When a doorbell rings (`dialmatrix.ring` service), the integration reads the enabled switches and fans out notifications automatically
- Fires a `dialmatrix_ring` event so automations can react to ring events as well
- A companion [Lovelace card](https://github.com/NINE78/corte-dialmatrix-card) provides a visual grid to toggle routing cells on/off

## Installation

Install via [HACS](https://hacs.xyz) by adding this repository as a custom integration repository.

## Configuration

Add to `configuration.yaml`:

```yaml
dialmatrix:
  doorbells:
    - id: front
      name: "Front Door"
    - id: garden
      name: "Garden Gate"
    - id: garage
      name: "Garage"
  targets:
    - id: alice_phone
      name: "Alice"
      notify_service: notify.mobile_app_alice_iphone
    - id: bob_phone
      name: "Bob"
      notify_service: notify.mobile_app_bob_pixel
      notify_message: "Doorbell at the $doorbell_name!"
    - id: living_room
      name: "Living Room Speaker"
      tts_entity: tts.google_en_com
      tts_media_player: media_player.living_room_speaker
```

### Target options

| Key | Required | Description |
|---|---|---|
| `id` | ✅ | Unique identifier used internally |
| `name` | ✅ | Display name shown in the Lovelace card |
| `notify_service` | | HA notify service, e.g. `notify.mobile_app_foo` |
| `notify_title` | | Push notification title. Default: `Doorbell` |
| `notify_message` | | Push notification body. Supports `$doorbell_name`. Default: `Someone is at the $doorbell_name door` |
| `tts_entity` | | TTS entity, e.g. `tts.google_en_com` |
| `tts_media_player` | | Target media player entity ID |
| `tts_message` | | TTS message text. Supports `$doorbell_name` |

## Automations

You need one automation per doorbell to trigger the ring service from MQTT:

```yaml
automation:
  - alias: "Doorbell — Front Door"
    trigger:
      - platform: mqtt
        topic: doorbell/front
    action:
      - service: dialmatrix.ring
        data:
          doorbell_id: front
```

## Events

Every ring also fires a `dialmatrix_ring` event on the HA event bus:

```json
{
  "doorbell_id": "front",
  "doorbell_name": "Front Door",
  "enabled_targets": ["alice_phone", "living_room"]
}
```

## Lovelace card

Install the companion [Dial Matrix Card](https://github.com/NINE78/corte-dialmatrix-card) from HACS, then add to your dashboard:

```yaml
type: custom:dialmatrix-card
title: "Call Routing Matrix"
```
