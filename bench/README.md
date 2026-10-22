# Benchmark

- `hallucinations/`: code that must be flagged. Each file marks the expected
  finding: `# ghostcall-expect: GC004 12` (add `medium` for medium confidence).
- `clean/`: valid code that must produce zero findings.
- `requirements.txt`: the pinned versions every case was verified against.

## Run

```bash
uv venv bench/envs/main --python 3.12
uv pip install --python bench/envs/main/bin/python -r bench/requirements.txt
ghostcall bench --python bench/envs/main/bin/python [--include-medium]
```

On Windows use `bench\envs\main\Scripts\python.exe`. The exit code is `0` only
with no misses and no false positives.

## Add a case

1. Confirm the line really fails in the pinned environment (run it).
2. Add it to `hallucinations/` with a marker: `frame.append(x)  #?GC003 medium`.
3. Run `python bench/tools/mark_expectations.py bench/hallucinations` to turn
   markers into line-numbered expectations.

Known misses go in `clean/` with a note explaining why.
