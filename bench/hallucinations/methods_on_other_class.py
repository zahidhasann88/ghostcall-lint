"""Methods that exist in the library, but on a different class.

``raise_for_status`` belongs to the *response* in both requests and httpx, and
``.str`` is a ``Series`` accessor; each line raises AttributeError in the pinned
install.  Instance tracking finds them, so they are medium confidence.
"""

import httpx
import pandas as pd
import requests

session = requests.Session()
# ghostcall-expect: GC003 14 medium
session.raise_for_status()
client = httpx.Client()
# ghostcall-expect: GC003 17 medium
client.raise_for_status()
frame = pd.DataFrame({"a": ["x"]})
# ghostcall-expect: GC003 20 medium
upper = frame.str
print(upper)
