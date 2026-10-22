# Rules

Run `ghostcall explain CODE` for the same information in the terminal.
Every rule skips anything it cannot prove.

| code | title | severity |
| --- | --- | --- |
| `GC000` | file could not be parsed | warning |
| `GC001` | module not installed | error |
| `GC002` | name not found in module | error |
| `GC003` | attribute not found | error |
| `GC004` | unknown keyword argument | error |
| `GC005` | too many positional arguments | error |
| `GC006` | missing required argument | error |
| `GC007` | deprecated API | warning |

## GC000 — file could not be parsed

The file has a syntax error, so it is skipped. Does not change the exit code.

**Skipped when:** disabled with `--ignore GC000`

```python
def broken(:
```

## GC001 — module not installed

An imported module is not installed, not in the stdlib, and not local.

**Skipped when:** the import is guarded (try/except ImportError, TYPE_CHECKING, version or platform check), relative, or in ignore-modules

```python
import pandasx
```

## GC002 — name not found in module

`from module import name` where the installed module has no `name`.

**Skipped when:** the module builds names at runtime or could not be read completely

```python
from pandas import read_csvv
```

## GC003 — attribute not found

An attribute or method that the installed library does not define. Medium confidence when found through instance tracking or a dynamic base class.

**Skipped when:** the object builds attributes at runtime or could not be read completely

```python
import pandas as pd

pd.read_csvv("f.csv")
```

## GC004 — unknown keyword argument

A keyword argument the installed signature does not accept.

**Skipped when:** the function takes `**kwargs`, the call uses `**mapping`, or a decorator may change the signature

```python
import pandas as pd

pd.read_csv("f.csv", skip_blank=True)
```

## GC005 — too many positional arguments

More positional arguments than the installed signature accepts.

**Skipped when:** the function takes `*args` or the call uses `*sequence`

```python
import json

json.dumps({}, None, None, None, None, None, "x", "y")
```

## GC006 — missing required argument

A required argument of the installed signature is not passed.

**Skipped when:** the call uses `*args` or `**kwargs` that could supply it

```python
import requests

requests.get()
```

## GC007 — deprecated API

The installed library marks the API as deprecated (`@deprecated` or a `.. deprecated::` docstring).

**Skipped when:** the deprecation is only mentioned in prose

```python
import pandas as pd

pd.DataFrame({"a": [1]}).applymap(str)
```
