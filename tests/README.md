# Tests

Lightweight tests that run without a Home Assistant install: `stubs/` provides
minimal stand-ins for the `homeassistant` modules the integration imports.

```sh
pip install voluptuous
python3 tests/test_backend.py
```
