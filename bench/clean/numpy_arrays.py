"""Valid numpy 2.2 code, including the dtype aliases that were removed in 1.24."""

import numpy as np
from numpy import ndarray

# ``np.float`` / ``np.int`` / ``np.asscalar`` were removed; the concrete dtypes
# and the documented constructors remain.
values = np.zeros((2, 3), dtype=np.float64)
ints = np.arange(6, dtype=np.int64).reshape(2, 3)
print(np.float64(1.5), np.int32(3))

total = np.sum(ints, axis=1, keepdims=True)
print(np.linalg.norm(values, ord="fro", axis=0, keepdims=False))
print(np.stack([values, values], axis=-1))
print(np.einsum("ij,ij->i", ints, ints, optimize=True))

rng = np.random.default_rng(seed=7)
sample = rng.normal(loc=0.0, scale=1.0, size=(4, 5))
print(np.clip(sample, a_min=-1.0, a_max=1.0))
print(np.isclose(sample, 0.0, rtol=1e-5, atol=1e-8, equal_nan=True))

# ndarray methods and attribute-style access that static tools must not flag.
grid: ndarray = np.asarray([[1, 2], [3, 4]])
print(grid.shape, grid.dtype, grid.T, grid.sum(axis=0), grid.astype(np.float32))
print(np.ma.masked_invalid(sample).mean())

# ``np.asscalar`` is gone, ``.item()`` replaces it.
print(sample.sum().item())
