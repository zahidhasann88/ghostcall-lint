"""A documented false negative, kept in ``clean/`` on purpose.

``numpy.zeros`` is a C-level constructor: numpy's own stub declares it as an
opaque ``_ConstructorEmpty`` protocol instance, so ghostcall cannot read a
signature *or* a return type for it.  ``grid`` therefore has no known type and
``grid.reshapez(...)`` is never reported -- the two lines below are the exact
code ghostcall is expected to miss.  Guessing here is what would produce false
positives, so silence is the designed behaviour.
"""

import numpy as np

grid = np.zeros((2, 2))
reshaped = grid.reshapez(4)
size = grid.shapez
print(grid.reshape(4), reshaped, size, grid.mean(axis=0))
