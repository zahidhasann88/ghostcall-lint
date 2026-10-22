"""Real names imported from the wrong module of the right library.

Verified against the pinned install by running each import: ``create_engine``
lives in ``sqlalchemy`` (``sqlalchemy.orm`` never imports it), and ``Session`` in
``requests.sessions`` (``requests.models`` does not import it).
"""

# ghostcall-expect: GC002 9
from sqlalchemy.orm import create_engine
# ghostcall-expect: GC002 11
from requests.models import Session

print(create_engine, Session)
