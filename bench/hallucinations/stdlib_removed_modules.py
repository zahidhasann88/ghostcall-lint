"""Modules and names removed from the standard library (target: Python 3.12)."""

# ghostcall-expect: GC001 4
import imp
# ghostcall-expect: GC001 6
import distutils.core

# ghostcall-expect: GC002 9
from itertools import izip

try:
    izip
except NameError:  # pragma: no cover
    izip = None
print(imp, distutils.core, izip)
