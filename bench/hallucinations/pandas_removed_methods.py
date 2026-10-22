"""Pandas 1.x idioms that pandas 2.2.3 removed.

Every expectation here was verified by resolving the call against the pinned
environment (``ghostcall info pandas.DataFrame.append``).
"""

import pandas as pd

frame = pd.DataFrame({"a": [1, 2]})
extra = pd.DataFrame({"a": [3]})

# ghostcall-expect: GC003 13 medium
frame.append(extra)
# ghostcall-expect: GC003 15 medium
frame.reindex_axis(["a"], axis=0)
# ghostcall-expect: GC003 17 medium
frame.read_table("data.tsv")

frame.reset_index(drop=True)
pd.concat([frame, extra], ignore_index=True)
