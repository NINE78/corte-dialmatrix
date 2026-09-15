commands = {}
class ActiveConnection: ...
def websocket_command(schema):
    def deco(fn):
        fn._ws_schema = schema; fn._ws_type = schema[[k for k in schema if str(k) == "type"][0]]; return fn
    return deco
def require_admin(fn): return fn
def async_response(fn): return fn
def async_register_command(hass, fn): commands[fn._ws_type] = fn
