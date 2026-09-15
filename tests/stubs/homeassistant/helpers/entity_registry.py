class _Reg:
    def __init__(self): self.removed = []
    def async_get_entity_id(self, *a): return None
    def async_update_entity(self, *a, **k): pass
    def async_remove(self, entity_id): self.removed.append(entity_id)
REG = _Reg()
def async_get(hass): return REG
def async_entries_for_config_entry(registry, entry_id): return []
