"""Names that do not exist in the module a snippet pretends they came from."""

from collections import OrderedDict as OrderedDictOK
from typing import Any, Callable, Dict, List, Optional
# ghostcall-expect: GC002 6
from urllib.parse import urlparze
# ghostcall-expect: GC002 8
from pandas import DataFram
# ghostcall-expect: GC002 10
from requests import async_get as fetch

print(OrderedDictOK, Any, Callable, Dict, List, Optional, urlparze, DataFram, fetch)
