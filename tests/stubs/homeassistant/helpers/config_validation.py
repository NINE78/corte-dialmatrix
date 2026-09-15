import voluptuous as vol
def string(v):
    if v is None or isinstance(v, (list, dict)): raise vol.Invalid("value should be a string")
    return str(v)
def boolean(v): return bool(v)
def entity_id(v): return str(v)
def ensure_list(v):
    if v is None: return []
    return v if isinstance(v, list) else [v]
