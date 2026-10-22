"""Requests calls that a code generator tends to invent."""

import requests

# ghostcall-expect: GC006 6
response = requests.get()
# ghostcall-expect: GC004 8
retried = requests.get("https://x.invalid", retries=3)
# ghostcall-expect: GC003 10
async_get = requests.async_get
# ghostcall-expect: GC003 12
timeout_error = requests.exceptions.ConnectionTimeout

print(response, retried, async_get, timeout_error)
