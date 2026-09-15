class SwitchEntity:
    _attr_unique_id = None
    _attr_device_info = None
    _attr_icon = None
    @property
    def unique_id(self): return self._attr_unique_id
    @property
    def device_info(self): return self._attr_device_info
    @property
    def icon(self): return self._attr_icon
    def async_write_ha_state(self): pass
