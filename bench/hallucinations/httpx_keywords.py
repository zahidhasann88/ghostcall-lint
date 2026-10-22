"""httpx 0.28 has no ``retry=`` shortcut and no ``bogus_option``."""

import httpx

# ghostcall-expect: GC004 6
response = httpx.get("https://x.invalid", retry=3)
# ghostcall-expect: GC004 8
client = httpx.Client(timeout=5.0, bogus_option=True)
# ghostcall-expect: GC003 10 medium
stream = client.streamz("GET", "https://x.invalid")
print(response, client, stream)
