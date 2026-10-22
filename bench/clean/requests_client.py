"""Valid requests 2.32 usage: every keyword below exists in the installed stubs."""

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = Session()
session.mount(
    "https://",
    HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=Retry(total=3)),
)
session.headers.update({"Accept": "application/json"})

# ``requests.get(url, ...)`` with a real timeout tuple, not the invented
# ``retries=`` keyword that the old REST-client snippet used.
response: Response = requests.get(
    "https://example.invalid/items",
    params={"page": 1},
    timeout=(3.05, 27),
    allow_redirects=True,
    verify=True,
)
response.raise_for_status()
payload = response.json()

# POST/DELETE signatures, including the data/json split.
created = requests.post(
    "https://example.invalid/items",
    json={"name": "widget"},
    timeout=10,
    headers={"Content-Type": "application/json"},
)
requests.delete("https://example.invalid/items/1", auth=("user", "pass"), timeout=5)
requests.patch("https://example.invalid/items/1", data={"name": "gadget"}, timeout=5)
requests.put("https://example.invalid/items/1", json={"name": "gadget"})

# Session-level calls and streamed reading.
prepared = session.prepare_request(requests.Request("GET", "https://example.invalid/x").prepare())
streamed = session.send(prepared, stream=True, timeout=30, verify=False)
for chunk in streamed.iter_content(chunk_size=8192):
    if chunk:
        break

# Exception classes that exist in requests.exceptions.
try:
    requests.head("https://example.invalid", timeout=1)
except requests.exceptions.ConnectionError as error:
    print(error)
except requests.exceptions.Timeout:
    pass
except requests.exceptions.RequestException:
    pass

session.close()
del payload, created
