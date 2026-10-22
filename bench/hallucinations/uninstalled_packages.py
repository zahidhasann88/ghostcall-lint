"""Packages that plain are not installed in the target environment."""

# ghostcall-expect: GC001 4
import polars
# ghostcall-expect: GC001 6
import dask.dataframe

frame = polars.read_csv("data.csv")
print(frame, dask.dataframe)
