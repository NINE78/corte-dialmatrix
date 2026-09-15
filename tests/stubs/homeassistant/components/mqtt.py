subscriptions = {}
unsubscribed = []
available = True
async def async_wait_for_mqtt_client(hass): return available
async def async_subscribe(hass, topic, cb):
    subscriptions[topic] = cb
    def unsub(): unsubscribed.append(topic)
    return unsub
