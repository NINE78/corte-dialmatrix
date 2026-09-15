SOURCE_IMPORT = "import"

class ConfigEntry:
    def __init__(self, options=None, entry_id="entry1"):
        self.options = options or {}; self.entry_id = entry_id; self.data = {}
        self._listeners = []; self._on_unload = []
    def add_update_listener(self, fn): self._listeners.append(fn); return lambda: None
    def async_on_unload(self, fn): self._on_unload.append(fn)

class _FlowBase:
    def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}
    def async_show_menu(self, step_id, menu_options):
        return {"type": "menu", "step_id": step_id, "menu_options": menu_options}
    def async_create_entry(self, title, data, options=None):
        return {"type": "create_entry", "title": title, "data": data, "options": options}
    def async_abort(self, reason): return {"type": "abort", "reason": reason}
    def add_suggested_values_to_schema(self, schema, suggested): return schema

class ConfigFlow(_FlowBase):
    _entries = []
    def __init_subclass__(cls, domain=None, **kw): super().__init_subclass__(**kw)
    def _async_current_entries(self): return ConfigFlow._entries

class OptionsFlow(_FlowBase): pass
