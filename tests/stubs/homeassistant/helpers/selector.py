class _Sel:
    def __init__(self, *a, **k): pass
    def __call__(self, v): return v
class TextSelector(_Sel): pass
class TextSelectorConfig(dict):
    def __init__(self, **k): super().__init__(**k)
class TextSelectorType:
    TEXT = "text"
class SelectSelector(_Sel): pass
class SelectSelectorConfig(dict):
    def __init__(self, **k): super().__init__(**k)
class SelectOptionDict(dict):
    def __init__(self, **k): super().__init__(**k)
class BooleanSelector(_Sel): pass
class ObjectSelector(_Sel): pass
class EntitySelector(_Sel): pass
class EntitySelectorConfig(dict):
    def __init__(self, **k): super().__init__(**k)
