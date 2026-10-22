"""APIs that still exist in pandas 2.2.3 but are documented as deprecated.

``DataFrame.applymap``, ``DataFrame.to_gbq`` and ``NDFrame.pad`` carry an
object-level ``.. deprecated::`` directive, so they are reported.  ``ffill`` and
``fillna`` mention deprecations inside their ``Parameters`` section only and must
stay silent.
"""

import pandas as pd

frame = pd.DataFrame({"a": [1, 2]})
# ghostcall-expect: GC007 13
frame.applymap(str)
# ghostcall-expect: GC007 15
frame.to_gbq("project.dataset.table")
# ghostcall-expect: GC007 17
frame.pad()

frame.ffill()
frame.fillna(value=0)
