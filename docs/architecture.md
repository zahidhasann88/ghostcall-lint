# How ghostcall works

```
env.py  →  index.py  →  scope.py + resolve.py  →  signature.py + checks.py  →  report.py
 find       read the     what does each name       compare the call with      print
 the env    installed    in the file refer to?     the installed signature    findings
            APIs
```

1. **Environment** (`env.py`) runs one fixed script in the target interpreter
   (`python -I -S`) to get its version, site-packages and installed versions.
   `-S` matters: it stops `.pth` files from running any code.
2. **Index** (`index.py`, `astindex.py`) reads library APIs from files, never by
   importing them: griffe for `.py` source, the AST for `.pyi` stubs, typeshed for
   the stdlib. Only modules your code imports are read, and results are cached.
3. **Resolution** (`scope.py`, `resolve.py`) maps each expression, like
   `pd.read_csv`, to the installed object. Anything uncertain is `unknown`, with
   a reason (shadowed name, star import, dynamic module, ...).
4. **Checks** (`signature.py`, `checks.py`) compare each call with the signature
   and apply the rules, suppressions and config.
5. **Report** (`report.py`, `diffmode.py`) prints text, JSON, SARIF or GitHub
   annotations, optionally limited to changed lines.

## Staying precise

- **Prove or skip.** Every rule has a "skipped when" condition (`ghostcall explain`).
- **Absence needs a complete namespace.** A missing name is only reported when the
  whole module or class could be read.
- **Inference is medium confidence.** Instance tracking (`df = pd.DataFrame()`)
  is hidden unless `--include-medium` is used.
- **Decorators must be trusted.** A decorator is trusted only if it is a known
  pass-through (`functools.wraps`, `lru_cache`, ...), or if its source returns the
  function unchanged. Otherwise the call is skipped.
- **Nothing is executed.** `tests/test_import_free.py` and `tests/test_env.py`
  prove it with marker files.
