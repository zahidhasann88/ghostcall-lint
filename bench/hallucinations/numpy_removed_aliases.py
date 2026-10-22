"""NumPy aliases removed in 1.24 (the pinned environment runs NumPy 2.2.2)."""

import numpy as np

values = np.array([1.0, 2.0])
# ghostcall-expect: GC003 7
total = np.float(values.sum())
# ghostcall-expect: GC003 9
count = np.int(values.size)
# ghostcall-expect: GC003 11
flag = np.bool8(True)
# ghostcall-expect: GC003 13
alias = np.float_(1.0)
# ghostcall-expect: GC003 15
zero = np.int0(1.0)
print(np.float64(total), np.int64(count), flag, alias, zero)
