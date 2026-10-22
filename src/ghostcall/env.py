from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PROBE_SOURCE",
    "Distribution",
    "EnvError",
    "EnvNotFound",
    "EnvUnusable",
    "Environment",
    "discover",
    "find_python",
]

_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "site-packages",
        "venv",
        ".venv",
        "env",
        ".env",
        "wheelhouse",
    }
)

PROBE_SOURCE = r"""
import inspect, json, os, sys, sysconfig, site
try:
    import importlib.metadata as md
except Exception:
    md = None

paths = sysconfig.get_paths()
base_prefix = getattr(sys, "base_prefix", sys.prefix)
venv_prefix = None
exe_dir = os.path.dirname(os.path.abspath(sys.executable))
for candidate in (exe_dir, os.path.dirname(exe_dir)):
    if os.path.isfile(os.path.join(candidate, "pyvenv.cfg")):
        venv_prefix = candidate
        break
if venv_prefix is None and sys.prefix != base_prefix:
    venv_prefix = sys.prefix
in_venv = venv_prefix is not None
system_site = not in_venv
if in_venv:
    try:
        with open(os.path.join(venv_prefix, "pyvenv.cfg"), encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition("=")
                if key.strip().lower() == "include-system-site-packages":
                    system_site = value.strip().lower() == "true"
    except OSError:
        pass

def site_dirs(prefixes):
    try:
        found = site.getsitepackages(prefixes)
    except Exception:
        return []
    return [p for p in found if os.path.basename(p) in ("site-packages", "dist-packages")]

def venv_site_dirs(prefix):
    version = "python%d.%d" % sys.version_info[:2]
    found = [os.path.join(prefix, "Lib", "site-packages")]
    for libdir in dict.fromkeys([getattr(sys, "platlibdir", "lib"), "lib", "lib64"]):
        for name in (version, version + "t"):
            found.append(os.path.join(prefix, libdir, name, "site-packages"))
    return found

candidates = []
if system_site:
    try:
        candidates.append(site.getusersitepackages())
    except Exception:
        pass
if in_venv:
    candidates += venv_site_dirs(venv_prefix)
if system_site:
    candidates += [paths.get("purelib"), paths.get("platlib")]
    candidates += site_dirs([base_prefix, getattr(sys, "base_exec_prefix", base_prefix)])
site_packages = []
seen = set()
for candidate in candidates:
    if not candidate or not os.path.isdir(candidate):
        continue
    real = os.path.realpath(candidate)  # venvs symlink lib64 -> lib
    if real not in seen:
        seen.add(real)
        site_packages.append(candidate)

out = {
    "executable": sys.executable,
    "version_info": list(sys.version_info[:3]),
    "sys_prefix": venv_prefix or sys.prefix,
    "base_prefix": base_prefix,
    "purelib": paths.get("purelib"),
    "platlib": paths.get("platlib"),
    "stdlib": paths.get("stdlib"),
    "platstdlib": paths.get("platstdlib"),
    "site_packages": site_packages,
    "platform": sys.platform,
    "implementation_name": sys.implementation.name,
    "implementation_version": list(sys.implementation.version[:2]),
    "path_entries": [p for p in sys.path if p],
    "stdlib_module_names": sorted(getattr(sys, "stdlib_module_names", [])),
    "packages_distributions": {},
    "dist_versions": {},
    "dist_top_levels": {},
}

def top_level_names(dist):
    # Same answer as importlib.metadata.packages_distributions(), restricted to
    # the site directories above.
    try:
        text = dist.read_text("top_level.txt") or ""
    except Exception:
        text = ""
    declared = [line.strip() for line in text.splitlines() if line.strip()]
    if declared:
        return declared, declared
    inferred = set()
    for file in dist.files or ():
        parts = file.parts
        name = parts[0] if len(parts) > 1 else inspect.getmodulename(str(file))
        if name and "." not in name:
            inferred.add(name)
    return sorted(inferred), []

if md is not None:
    try:
        dists = list(md.distributions(path=site_packages))
    except Exception:
        dists = []
    for dist in dists:
        try:
            name = dist.metadata["Name"]
            if not name or name in out["dist_versions"]:
                continue
            out["dist_versions"][name] = dist.version
            names, declared = top_level_names(dist)
            for top in names:
                out["packages_distributions"].setdefault(top, []).append(name)
            if declared:
                out["dist_top_levels"][name] = declared
        except Exception:
            continue
print(json.dumps(out))
"""


def _three_tuple(value: object) -> tuple[int, int, int]:
    """Coerce a serialised ``(major, minor, micro)`` list back into a tuple."""
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return int(value[0]), int(value[1]), int(value[2])
        except (TypeError, ValueError):
            pass
    return (0, 0, 0)


def _version_tuple(value: object) -> tuple[int, int]:
    """Coerce a serialised ``(major, minor, ...)`` list back into a tuple."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return (3, 11)


class EnvError(Exception):
    """Base class for environment problems (mapped to exit code 3)."""

    exit_code = 3


class EnvNotFound(EnvError):
    """No usable Python environment could be located."""


class EnvUnusable(EnvError):
    """An environment was located but could not be queried."""


@dataclass(frozen=True)
class Distribution:
    """An installed distribution mapped onto the import name(s) it provides."""

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class Environment:
    """A resolved target environment.

    Instances are immutable and cheap to copy; they contain no live objects from
    the target interpreter.
    """

    python: str
    version_info: tuple[int, int, int]
    site_packages: tuple[Path, ...]
    search_paths: tuple[Path, ...]
    stdlib_paths: tuple[Path, ...]
    prefix: str
    base_prefix: str
    platform: str
    implementation_name: str = "cpython"
    implementation_version: tuple[int, int] = (3, 11)
    stdlib_module_names: frozenset[str] = frozenset()
    _import_to_dist: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    _dist_versions: Mapping[str, str] = field(default_factory=dict)
    _local_modules: Mapping[str, Path] = field(default_factory=dict)
    _editable_roots: tuple[Path, ...] = ()
    extra_search_paths: tuple[Path, ...] = ()

    @property
    def version(self) -> tuple[int, int]:
        return (self.version_info[0], self.version_info[1])

    @property
    def version_string(self) -> str:
        return ".".join(str(part) for part in self.version_info)

    @property
    def is_venv(self) -> bool:
        return self.prefix != self.base_prefix

    @property
    def all_search_paths(self) -> tuple[Path, ...]:
        """Every directory that may contain importable modules."""
        seen: dict[str, Path] = {}
        for path in (*self.site_packages, *self.extra_search_paths, *self._editable_roots):
            seen.setdefault(str(path), path)
        return tuple(seen.values())

    def distributions_for(self, import_name: str) -> tuple[Distribution, ...]:
        """Distributions providing ``import_name`` (top-level import name)."""
        names = self._import_to_dist.get(import_name, ())
        out: list[Distribution] = []
        for name in names:
            version = self._dist_versions.get(name, "")
            out.append(Distribution(name=name, version=version))
        return tuple(out)

    def distribution_for(self, import_name: str) -> Distribution | None:
        dists = self.distributions_for(import_name)
        return dists[0] if dists else None

    def version_of_import(self, import_name: str) -> str | None:
        dist = self.distribution_for(import_name)
        return dist.version if dist else None

    def label_for(self, module: str) -> str:
        """Human readable ``name version`` label used in findings."""
        top = module.split(".", 1)[0]
        dist = self.distribution_for(top)
        if dist is not None and dist.version:
            return f"{dist.name} {dist.version}"
        if dist is not None:
            return dist.name
        return f"{top} (stdlib) {'.'.join(str(p) for p in self.version)}"

    @property
    def installed_top_levels(self) -> frozenset[str]:
        return frozenset(self._import_to_dist)

    @property
    def local_modules(self) -> Mapping[str, Path]:
        """Mapping of top-level project-local module name to its path."""
        return self._local_modules

    def is_local_module(self, top_level: str) -> bool:
        return top_level in self._local_modules

    def local_module_path(self, name: str) -> Path | None:
        return self._local_modules.get(name.split(".", 1)[0])

    def is_stdlib(self, top_level: str) -> bool:
        return top_level in self.stdlib_module_names

    @classmethod
    def from_python(
        cls,
        python: str | Path,
        *,
        source_roots: Sequence[Path] = (),
        extra_search_paths: Sequence[Path] = (),
    ) -> Environment:
        """Query ``python`` for its environment description.

        Raises :class:`EnvUnusable` when the interpreter cannot be queried.
        """
        exe = str(python)
        resolved = shutil.which(exe) or exe
        try:
            proc = subprocess.run(
                [resolved, "-I", "-S", "-c", PROBE_SOURCE],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise EnvUnusable(f"could not run {resolved!r}: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else "no output"
            raise EnvUnusable(f"{resolved!r} failed to describe its environment: {tail}")
        try:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise EnvUnusable(f"{resolved!r} produced unreadable environment information") from exc

        site_packages = _unique_paths(data.get("site_packages") or [])
        if not site_packages:
            raise EnvUnusable(
                f"{resolved!r} reports no site-packages directory; "
                "is it a full Python installation?"
            )
        search: list[Path] = []
        for entry in data.get("path_entries") or []:
            path = Path(entry)
            if path.is_dir() and str(path) not in {str(p) for p in site_packages}:
                search.append(path)
        editable_roots = _editable_install_roots(site_packages)
        env = cls(
            python=resolved,
            version_info=_three_tuple(data.get("version_info")),
            site_packages=site_packages,
            search_paths=tuple(search),
            stdlib_paths=_unique_paths(
                [p for p in (data.get("stdlib"), data.get("platstdlib")) if p]
            ),
            prefix=data.get("sys_prefix") or "",
            base_prefix=data.get("base_prefix") or "",
            platform=data.get("platform") or sys.platform,
            implementation_name=data.get("implementation_name") or "cpython",
            implementation_version=_version_tuple(data.get("implementation_version")),
            stdlib_module_names=frozenset(data.get("stdlib_module_names") or ()),
            _import_to_dist=_build_import_map(data),
            _dist_versions={str(k): str(v) for k, v in (data.get("dist_versions") or {}).items()},
            _editable_roots=tuple(editable_roots),
            extra_search_paths=tuple(Path(p) for p in extra_search_paths),
        )
        local = _discover_local_modules(source_roots, editable_roots)
        return env if not local else _with_local_modules(env, local)


def _with_local_modules(env: Environment, local: Mapping[str, Path]) -> Environment:
    from dataclasses import replace

    return replace(env, _local_modules=dict(local))


def _unique_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    seen: dict[str, Path] = {}
    for path in paths:
        if not path:
            continue
        p = Path(path)
        seen.setdefault(str(p), p)
    return tuple(seen.values())


def _build_import_map(data: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    mapping = data.get("packages_distributions") or {}
    out: dict[str, tuple[str, ...]] = {}
    if isinstance(mapping, Mapping):
        for import_name, dists in mapping.items():
            if isinstance(dists, str):
                out[str(import_name)] = (dists,)
            elif isinstance(dists, (list, tuple)):
                out[str(import_name)] = tuple(str(d) for d in dists)
    # Augment with top_level.txt data for distributions that do not register
    # through RECORD (editable installs and a few legacy build backends).
    tops = data.get("dist_top_levels") or {}
    if isinstance(tops, Mapping):
        for dist_name, names in tops.items():
            if not isinstance(names, (list, tuple)):
                continue
            for name in names:
                out.setdefault(str(name), ())
                existing = out[str(name)]
                if str(dist_name) not in existing:
                    out[str(name)] = (*existing, str(dist_name))
    return out


_PTH_DIR_RE = re.compile(r"^__editable__\..*_finder\.py$|^__editable__.*\.pth$")


def _editable_install_roots(site_packages: Sequence[Path]) -> list[Path]:
    """Find source roots of editable installs (``.pth`` / ``__editable__`` files)."""
    roots: list[Path] = []
    for site in site_packages:
        if not site.is_dir():
            continue
        try:
            entries = sorted(site.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".pth":
                try:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith(("#", "import ")):
                        continue
                    candidate = Path(line)
                    if candidate.is_dir():
                        roots.append(candidate)
                # setuptools' editable installs ship a finder module; read its
                # MAPPING to learn where the package actually lives.
                roots.extend(_roots_from_finder_module(site, entry.stem))
            elif _PTH_DIR_RE.match(entry.name) and entry.suffix == ".py":
                roots.extend(_roots_from_finder_module(site, entry.stem))
    return list(dict.fromkeys(roots))


def _roots_from_finder_module(site: Path, stem: str) -> list[Path]:
    """Parse ``__editable___x_finder.py`` MAPPING for source directories."""
    path = site / f"{stem}.py"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    roots: list[Path] = []
    match = re.search(r"MAPPING\s*=\s*(\{.*?\})", text, re.DOTALL)
    if match:
        roots.extend(_paths_from_literal(match.group(1)))
    for match in re.finditer(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", text):
        value = match.group(2)
        if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
            candidate = Path(value)
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def _paths_from_literal(text: str) -> list[Path]:
    out: list[Path] = []
    for match in re.finditer(r"['\"]([^'\"]+)['\"]", text):
        candidate = Path(match.group(1))
        if candidate.is_absolute() and candidate.is_dir():
            out.append(candidate)
    return out


def _discover_local_modules(
    source_roots: Sequence[Path], extra_roots: Sequence[Path] = ()
) -> dict[str, Path]:
    """Map top-level module names of the *project under analysis* to paths."""
    found: dict[str, Path] = {}
    for root in (*source_roots, *extra_roots):
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in _IGNORED_DIR_NAMES:
                continue
            if entry.is_file() and entry.suffix == ".py":
                found.setdefault(entry.stem, entry)
            elif entry.is_dir() and (
                (entry / "__init__.py").is_file() or _contains_python(entry, depth=2)
            ):
                found.setdefault(name, entry)
    return found


def _contains_python(directory: Path, depth: int = 2) -> bool:
    if depth <= 0:
        return False
    try:
        for entry in directory.iterdir():
            if entry.name.startswith((".", "__pycache__")):
                continue
            if entry.is_file() and entry.suffix in {".py", ".pyi"}:
                return True
            if entry.is_dir() and _contains_python(entry, depth - 1):
                return True
    except OSError:
        return False
    return False


def find_python(explicit: str | Path | None = None, start: Path | None = None) -> str:
    """Locate the interpreter to inspect.

    Order: ``explicit`` argument, ``./.venv``, ``$VIRTUAL_ENV``, ``./venv``,
    then the interpreter running ghostcall.  Raises :class:`EnvNotFound` if an
    explicitly requested interpreter does not exist.
    """
    if explicit:
        candidate = Path(explicit)
        if candidate.is_dir():
            candidate = candidate / _bin_dir() / _python_name()
        if not candidate.exists():
            raise EnvNotFound(f"no such interpreter: {explicit}")
        return str(candidate)

    base = start or Path.cwd()
    for name in (".venv", "venv"):
        candidate = base / name / _bin_dir() / _python_name()
        if candidate.exists():
            return str(candidate)
    env_var = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX")
    if env_var:
        candidate = Path(env_var) / _bin_dir() / _python_name()
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _bin_dir() -> str:
    return "Scripts" if os.name == "nt" else "bin"


def _python_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def discover(
    python: str | Path | None = None,
    *,
    source_roots: Sequence[Path] = (),
    extra_search_paths: Sequence[Path] = (),
    start: Path | None = None,
) -> Environment:
    """Discover the target environment.

    :raises EnvNotFound: when no interpreter can be located.
    :raises EnvUnusable: when the interpreter cannot describe itself.
    """
    try:
        resolved = find_python(python, start=start)
    except EnvNotFound:
        raise
    return Environment.from_python(
        resolved, source_roots=source_roots, extra_search_paths=extra_search_paths
    )
