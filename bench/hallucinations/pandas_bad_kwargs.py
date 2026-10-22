"""Keyword arguments that never existed, or were removed, in pandas 2.2.3.

``error_bad_lines`` and ``mangle_dupe_cols`` are gone; ``skip_blank`` is a
plausible invention of ``skip_blank_lines``.
"""

import pandas as pd

# ghostcall-expect: GC004 10
frame = pd.read_csv("data.csv", error_bad_lines=False)
# ghostcall-expect: GC004 12
table = pd.read_csv("data.csv", skip_blank=True, sep=",")
# ghostcall-expect: GC004 14
other = pd.read_csv("data.csv", mangle_dupe_cols=True)

print(frame, table, other)
