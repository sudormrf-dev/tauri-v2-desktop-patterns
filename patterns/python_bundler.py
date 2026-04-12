"""Python bundling strategies for Tauri v2 desktop apps.

Shipping Python with a Tauri desktop app requires choosing a bundling
strategy based on distribution requirements:

- EMBEDDED_VENV: Copy a virtual environment alongside the binary (simple,
  large bundle ~50-200 MB)
- PYINSTALLER: PyInstaller single-file or one-folder bundle (medium ~30-80 MB)
- NUITKA: Compiled Python via Nuitka (small ~5-20 MB, long build time)
- SYSTEM_PYTHON: Use system Python (no bundle, requires Python installed)
- CONDA_PACK: conda-pack environment (data science apps with numpy/torch)

Usage::

    config = BundleConfig(
        strategy=BundleStrategy.EMBEDDED_VENV,
        entry_point="src/sidecar/__main__.py",
        output_dir=Path("src-tauri/binaries"),
    )
    bundler = PythonBundler(config)
    info = bundler.validate()
    plan = bundler.build_plan()
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class BundleStrategy(str, Enum):
    """Python bundling strategy for Tauri desktop distribution."""

    EMBEDDED_VENV = "embedded_venv"
    PYINSTALLER = "pyinstaller"
    NUITKA = "nuitka"
    SYSTEM_PYTHON = "system_python"
    CONDA_PACK = "conda_pack"


@dataclass
class RuntimeInfo:
    """Information about the Python runtime to bundle.

    Attributes:
        executable: Path to the Python executable.
        version: Python version string (e.g. "3.12.3").
        platform: Platform tag (e.g. "linux-x86_64").
        is_venv: True if inside a virtual environment.
        venv_path: Path to the venv root (if is_venv).
        packages: List of installed package names.
    """

    executable: str
    version: str
    platform: str
    is_venv: bool
    venv_path: Path | None = None
    packages: list[str] = field(default_factory=list)

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        """Parse version string to (major, minor, patch)."""
        parts = self.version.split(".")
        try:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        except (IndexError, ValueError):
            return (0, 0, 0)


def detect_runtime() -> RuntimeInfo:
    """Detect the current Python runtime environment.

    Returns:
        :class:`RuntimeInfo` describing the active Python installation.
    """
    executable = sys.executable
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    machine = platform.machine().lower()
    system = platform.system().lower()
    plat = f"{system}-{machine}"

    is_venv = sys.prefix != sys.base_prefix
    venv_path = Path(sys.prefix) if is_venv else None

    return RuntimeInfo(
        executable=executable,
        version=version,
        platform=plat,
        is_venv=is_venv,
        venv_path=venv_path,
    )


@dataclass
class BundleConfig:
    """Configuration for bundling Python with a Tauri app.

    Attributes:
        strategy: Bundling strategy to use.
        entry_point: Python file or module that starts the sidecar.
        output_dir: Where to write the bundled binary/folder.
        app_name: Name used for the output binary.
        extra_packages: Additional packages to include beyond requirements.
        exclude_packages: Packages to exclude from the bundle.
        strip_debug: Remove debug symbols to reduce size.
        target_triple: Override platform target (e.g. "x86_64-pc-windows-msvc").
    """

    strategy: BundleStrategy = BundleStrategy.EMBEDDED_VENV
    entry_point: str = "sidecar/__main__.py"
    output_dir: Path = field(default_factory=lambda: Path("src-tauri/binaries"))
    app_name: str = "sidecar"
    extra_packages: list[str] = field(default_factory=list)
    exclude_packages: list[str] = field(default_factory=list)
    strip_debug: bool = True
    target_triple: str = ""


# Approximate bundle size estimates in MB per strategy
_SIZE_ESTIMATES_MB: dict[BundleStrategy, tuple[int, int]] = {
    BundleStrategy.EMBEDDED_VENV: (50, 200),
    BundleStrategy.PYINSTALLER: (30, 80),
    BundleStrategy.NUITKA: (5, 20),
    BundleStrategy.SYSTEM_PYTHON: (0, 0),
    BundleStrategy.CONDA_PACK: (200, 600),
}

# Relative build time multipliers (1.0 = fast)
_BUILD_TIME_FACTOR: dict[BundleStrategy, float] = {
    BundleStrategy.EMBEDDED_VENV: 1.0,
    BundleStrategy.PYINSTALLER: 2.0,
    BundleStrategy.NUITKA: 10.0,
    BundleStrategy.SYSTEM_PYTHON: 0.0,
    BundleStrategy.CONDA_PACK: 3.0,
}


class PythonBundler:
    """Generates build plans and validates bundling configuration.

    Does NOT actually invoke external tools — produces the shell
    commands and directory structure needed for CI/CD scripts.

    Args:
        config: Bundle configuration.
    """

    def __init__(self, config: BundleConfig | None = None) -> None:
        self._cfg = config or BundleConfig()

    @property
    def config(self) -> BundleConfig:
        """The bundler configuration."""
        return self._cfg

    def validate(self) -> list[str]:
        """Validate configuration and return a list of warnings.

        Returns:
            List of warning strings (empty = no issues).
        """
        warnings: list[str] = []

        if Path(self._cfg.entry_point).suffix != ".py":
            warnings.append(f"entry_point {self._cfg.entry_point!r} should end with .py")

        if self._cfg.strategy == BundleStrategy.SYSTEM_PYTHON:
            warnings.append("SYSTEM_PYTHON requires Python to be installed on the user's machine")

        if self._cfg.strategy == BundleStrategy.CONDA_PACK and not os.environ.get(
            "CONDA_DEFAULT_ENV"
        ):
            warnings.append("CONDA_PACK requires an active conda environment")

        return warnings

    def estimated_size_mb(self) -> tuple[int, int]:
        """Return (min_mb, max_mb) bundle size estimate."""
        return _SIZE_ESTIMATES_MB[self._cfg.strategy]

    def build_time_factor(self) -> float:
        """Relative build time multiplier for the selected strategy."""
        return _BUILD_TIME_FACTOR[self._cfg.strategy]

    def output_binary_name(self) -> str:
        """Platform-specific output binary name with Tauri triple suffix.

        Tauri expects sidecar binaries named: ``{app}-{target-triple}``
        e.g. ``sidecar-x86_64-unknown-linux-gnu``
        """
        triple = self._cfg.target_triple or _default_triple()
        name = f"{self._cfg.app_name}-{triple}"
        if platform.system() == "Windows":
            name += ".exe"
        return name

    def build_plan(self) -> list[str]:
        """Generate shell commands to bundle the Python sidecar.

        Returns:
            Ordered list of shell command strings.
        """
        strategy = self._cfg.strategy
        out = self._cfg.output_dir
        entry = self._cfg.entry_point
        name = self.output_binary_name()

        if strategy == BundleStrategy.EMBEDDED_VENV:
            return [
                f"python -m venv {out}/{self._cfg.app_name}-venv",
                f"{out}/{self._cfg.app_name}-venv/bin/pip install -r requirements.txt",
                f"cp {entry} {out}/",
                f"# Run via: {out}/{self._cfg.app_name}-venv/bin/python {entry}",
            ]

        if strategy == BundleStrategy.PYINSTALLER:
            strip_flag = "--strip" if self._cfg.strip_debug else ""
            return [
                "pip install pyinstaller",
                f"pyinstaller --onefile {strip_flag} --name {self._cfg.app_name} {entry}",
                f"cp dist/{self._cfg.app_name} {out}/{name}",
            ]

        if strategy == BundleStrategy.NUITKA:
            return [
                "pip install nuitka",
                (f"python -m nuitka --onefile --output-filename={name} --output-dir={out} {entry}"),
            ]

        if strategy == BundleStrategy.SYSTEM_PYTHON:
            return [
                "# No bundling needed — requires system Python",
                f"# Tauri will run: python {entry}",
            ]

        # CONDA_PACK
        return [
            "pip install conda-pack",
            f"conda-pack -o {out}/{self._cfg.app_name}-env.tar.gz",
            f"mkdir -p {out}/{self._cfg.app_name}-env",
            f"tar xf {out}/{self._cfg.app_name}-env.tar.gz -C {out}/{self._cfg.app_name}-env",
            f"{out}/{self._cfg.app_name}-env/bin/conda-unpack",
        ]


def _default_triple() -> str:
    """Return the Rust target triple for the current platform."""
    system = platform.system()
    machine = platform.machine().lower()

    arch_map = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }
    arch = arch_map.get(machine, machine)

    if system == "Linux":
        return f"{arch}-unknown-linux-gnu"
    if system == "Darwin":
        return f"{arch}-apple-darwin"
    if system == "Windows":
        return f"{arch}-pc-windows-msvc"
    return f"{arch}-unknown-unknown"
