"""Wrong arity: extra positional arguments that nothing can absorb."""

import json
from urllib.parse import urlparse

# ghostcall-expect: GC005 7
payload = json.dumps({"a": 1}, None, None, None, None)
# ghostcall-expect: GC005 9
parts = urlparse("https://x.invalid", "https", True, "extra")
print(payload, parts)
