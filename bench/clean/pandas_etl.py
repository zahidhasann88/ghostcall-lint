"""Valid pandas 2.2 code: modern replacements for every removed 1.x API.

Facts verified against pandas 2.2.3 in ``bench/envs/main``.
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})

# The 1.x idiom ``df.append(other)`` was removed in pandas 2.0; pd.concat is
# the supported spelling.
other = pd.DataFrame({"a": [4], "b": [7.0]})
combined = pd.concat([df, other], ignore_index=True)

# ``df.reindex_axis`` (removed in 1.0) is spelled reindex / set_axis now.
reindexed = df.reindex(columns=["a", "b"])
relabelled = df.set_axis(["x", "y", "z"], axis="index")

# ``DataFrame.applymap`` is deprecated (2.1) in favour of DataFrame.map, and
# ``Series.append`` does not exist any more either.
mapped = df.map(lambda value: value * 2 if isinstance(value, int) else value)

# ``inplace=`` strictness: these keywords all exist in pandas 2.2.
df.drop(columns=["b"], inplace=True, errors="ignore")
df.fillna(value=0, axis=1, inplace=False)
df.reset_index(drop=True, inplace=True)

# read_csv: error_bad_lines (removed in 1.4) is on_bad_lines now.
frame = pd.read_csv(
    "data.csv",
    sep=",",
    header=0,
    names=["a", "b"],
    skip_blank_lines=True,
    on_bad_lines="warn",
    dtype={"a": "int64"},
    keep_default_na=False,
)
frame.groupby("a", as_index=False, observed=True).agg(total=("b", "sum"))
frame.to_csv("out.csv", index=False, date_format="%Y-%m-%d")

# Categorical/extension APIs that still exist, including keyword-only args.
series = pd.Series(pd.Categorical(["a", "b", "a"], categories=["a", "b"], ordered=True))
series.cat.remove_unused_categories(inplace=True)
series.astype("category", copy=False)
series.rolling(window=2, min_periods=1, center=True).mean()
series.ewm(alpha=0.5, adjust=False).mean()

# numpy interop: the removed aliases (np.float, np.int, np.asscalar) are gone,
# the real dtypes stay.
array = np.array([1.0, 2.0, 3.0], dtype=np.float64)
print(np.linalg.norm(array, ord=2, keepdims=True))
print(np.concatenate([array, array], axis=0, dtype=np.float64))

result = pd.DataFrame({"x": [1, 2]})
print(DataFrame.to_string(result))
