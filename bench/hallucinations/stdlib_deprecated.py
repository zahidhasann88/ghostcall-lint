"""Standard-library APIs that are deprecated or gone in Python 3.12."""

from datetime import datetime, timezone
from unittest import TestCase

# ghostcall-expect: GC007 7
naive = datetime.utcnow()
# ghostcall-expect: GC007 9
stamp = datetime.utcfromtimestamp(0)
case = TestCase()
# ghostcall-expect: GC003 12 medium
case.assertEquals(1, 1)

print(naive, stamp, case, datetime.now(timezone.utc))
