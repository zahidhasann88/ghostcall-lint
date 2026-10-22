# ghostcall

Find API calls that don't exist in the library versions you actually have installed.

AI-written code often looks right but targets a different version:
`df.append(...)`, `requests.get(url, retries=3)`, `pd.read_csv(..., skip_blank=True)`.
ghostcall checks every call against your real environment and reports what breaks.

```console
$ ghostcall check src/
app.py:7:7 GC004 pandas.read_csv() got an unexpected keyword argument 'skip_blank'
    installed: pandas 2.2.3   signature: read_csv(filepath_or_buffer, *, sep=..., ...)
    did you mean: skip_blank_lines
```

- **Precise:** if it can't prove a problem, it stays silent (`--verbose` shows what it skipped).
- **Static by default:** your code and dependencies are never imported or run.
- **Version-aware:** findings are facts about the environment you point it at.

## Install

```bash
pip install ghostcall-lint    # the command is `ghostcall`
```

Needs Python 3.11+ to run; the code it checks can target any version.

## Usage

```bash
ghostcall check src/                         # uses ./.venv or $VIRTUAL_ENV
ghostcall check --python .venv src/          # or name the environment
ghostcall check --diff origin/main           # only lines changed vs a ref
ghostcall check --staged                     # only lines in the next commit
ghostcall check - < file.py                  # read from stdin
ghostcall explain GC004                      # what a rule means
ghostcall info pandas.read_csv               # show the installed signature
```

Useful flags: `--include-medium`, `--verbose`, `--format text|json|sarif|github`,
`--select/--ignore GC004`, `--jobs N`.
Exit codes: `0` clean, `1` findings, `2` usage error, `3` environment not found.

## Rules

| code | finds |
| --- | --- |
| GC001 | module not installed |
| GC002 | name not found in module (`from x import y`) |
| GC003 | attribute or method not found |
| GC004 | unknown keyword argument |
| GC005 | too many positional arguments |
| GC006 | missing required argument |
| GC007 | deprecated API (warning) |
| GC000 | file could not be parsed (warning) |

Details and examples: [docs/rules.md](docs/rules.md).
Findings that rely on inference (e.g. tracking `df = pd.DataFrame()`) are
*medium* confidence and only shown with `--include-medium`.

## Configuration

```toml
[tool.ghostcall]                  # in pyproject.toml; CLI flags win
python = ".venv/bin/python"
ignore = ["GC007"]
include-medium = true
ignore-modules = ["numpy"]
per-file-ignores = { "migrations/*" = ["GC003"] }
```

Silence one line with `# ghostcall: ignore` or `# ghostcall: ignore[GC004]`.

## Integrations

**pre-commit** (checks only the lines you're committing):

```yaml
- repo: https://github.com/zahidhasann88/ghostcall-lint
  rev: v0.1.0
  hooks:
    - id: ghostcall
      args: ["--python", ".venv/bin/python"]
```

**GitHub Actions** (annotates the PR diff, optional SARIF upload):

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: zahidhasann88/ghostcall-lint@v0.1.0
  with:
    requirements: requirements.txt
    paths: src
```

**AI coding tools:** pipe generated code in and feed the findings back.

```bash
ghostcall check --stdin-filename generated.py - < generated.py
```

**Python:**

```python
from ghostcall import check_source, discover

for finding in check_source(code, discover(".venv")):
    print(finding.render_line())
```

## Benchmark

Hand-written corpora in `bench/`: code that must be flagged, and valid code that
must stay silent. Run with `ghostcall bench`.

<!-- bench:start -->
Python 3.12 · pandas 2.2.3 · numpy 2.2.2 · requests 2.32.3 · httpx 0.28.1 · sqlalchemy 2.0.36 · click 8.1.8 · rich 13.9.4

| rule | default | `--include-medium` |
| --- | --- | --- |
| GC001 | 4/4 | 4/4 |
| GC002 | 6/6 | 6/6 |
| GC003 | 8/8 | 17/17 |
| GC004 | 11/11 | 12/12 |
| GC005 | 2/2 | 2/2 |
| GC006 | 1/1 | 1/1 |
| GC007 | 6/6 | 6/6 |
| **found** | **38/38** | **48/48** |
| **false positives** (14 clean files) | **0** | **0** |
<!-- bench:end -->

## Limitations

ghostcall skips anything it can't prove, so these are not checked:

- Union return types (`pd.read_csv(...)` returns `DataFrame | TextFileReader`).
- Reassigned names, function parameters, and other data flow.
- Calls through decorators that may change the signature (e.g. `@click.command()`).
- Modules that build their names at runtime (`__getattr__`, compiled extensions).
- Calls using `*args` / `**kwargs` unpacking.

`--allow-import` adds runtime introspection for unreadable modules, but it
executes library code, so it is off by default and prints a warning.

## Development

```bash
uv sync --all-extras
uv venv bench/envs/main --python 3.12
uv pip install --python bench/envs/main/bin/python -r bench/requirements.txt
uv run pytest && uv run ruff check src tests && uv run mypy
```

On Windows use `bench\envs\main\Scripts\python.exe`.
More: [how it works](docs/architecture.md) · [changelog](CHANGELOG.md) ·
license: MIT ([LICENSE](LICENSE)).
