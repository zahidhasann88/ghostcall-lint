"""A module that imports its own package: project code is never flagged."""

from projects.helpers import JsonStore, load_settings, with_retries
from projects.helpers import MISSING_ATTRIBUTE_THAT_STILL_MUST_NOT_BE_FLAGGED as _unused_but_local

settings = load_settings("config.toml")
store = JsonStore(root=".")
store.save("key", {"value": 1})
store.load("key", default={})

result = with_retries(lambda: store.load("key"), attempts=3, delay=0.1)
print(settings, result, _unused_but_local)
