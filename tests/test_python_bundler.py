"""Tests for python_bundler.py."""

from __future__ import annotations

from pathlib import Path

from patterns.python_bundler import (
    BundleConfig,
    BundleStrategy,
    PythonBundler,
    RuntimeInfo,
    detect_runtime,
)


class TestRuntimeInfo:
    def test_version_tuple_parsed(self):
        info = RuntimeInfo(
            executable="/usr/bin/python3",
            version="3.12.3",
            platform="linux-x86_64",
            is_venv=False,
        )
        assert info.version_tuple == (3, 12, 3)

    def test_version_tuple_invalid(self):
        info = RuntimeInfo(
            executable="/usr/bin/python3",
            version="bad",
            platform="linux-x86_64",
            is_venv=False,
        )
        assert info.version_tuple == (0, 0, 0)


class TestDetectRuntime:
    def test_returns_runtime_info(self):
        info = detect_runtime()
        assert isinstance(info, RuntimeInfo)
        assert info.executable
        assert info.version

    def test_version_has_three_parts(self):
        info = detect_runtime()
        major, _minor, _patch = info.version_tuple
        assert major >= 3


class TestBundleConfig:
    def test_defaults(self):
        cfg = BundleConfig()
        assert cfg.strategy == BundleStrategy.EMBEDDED_VENV
        assert cfg.app_name == "sidecar"

    def test_custom(self):
        cfg = BundleConfig(strategy=BundleStrategy.PYINSTALLER, app_name="myapp")
        assert cfg.strategy == BundleStrategy.PYINSTALLER
        assert cfg.app_name == "myapp"


class TestPythonBundler:
    def test_validate_no_warnings_for_valid_config(self):
        cfg = BundleConfig(strategy=BundleStrategy.EMBEDDED_VENV, entry_point="sidecar/__main__.py")
        bundler = PythonBundler(cfg)
        warnings = bundler.validate()
        # SYSTEM_PYTHON and CONDA_PACK produce warnings; EMBEDDED_VENV should not
        assert all("entry_point" not in w for w in warnings)

    def test_validate_system_python_warns(self):
        cfg = BundleConfig(strategy=BundleStrategy.SYSTEM_PYTHON)
        bundler = PythonBundler(cfg)
        warnings = bundler.validate()
        assert any("system" in w.lower() or "python" in w.lower() for w in warnings)

    def test_size_estimate_system_is_zero(self):
        cfg = BundleConfig(strategy=BundleStrategy.SYSTEM_PYTHON)
        bundler = PythonBundler(cfg)
        low, high = bundler.estimated_size_mb()
        assert low == 0
        assert high == 0

    def test_size_estimate_pyinstaller_is_nonzero(self):
        cfg = BundleConfig(strategy=BundleStrategy.PYINSTALLER)
        bundler = PythonBundler(cfg)
        low, high = bundler.estimated_size_mb()
        assert low > 0
        assert high >= low

    def test_build_time_system_is_zero(self):
        cfg = BundleConfig(strategy=BundleStrategy.SYSTEM_PYTHON)
        bundler = PythonBundler(cfg)
        assert bundler.build_time_factor() == 0.0

    def test_build_time_nuitka_is_highest(self):
        strategies = list(BundleStrategy)
        factors = {
            s: PythonBundler(BundleConfig(strategy=s)).build_time_factor() for s in strategies
        }
        assert factors[BundleStrategy.NUITKA] == max(factors.values())

    def test_output_binary_name_contains_app_name(self):
        cfg = BundleConfig(app_name="mysidecar")
        bundler = PythonBundler(cfg)
        name = bundler.output_binary_name()
        assert "mysidecar" in name

    def test_build_plan_embedded_venv_has_venv(self):
        cfg = BundleConfig(
            strategy=BundleStrategy.EMBEDDED_VENV,
            output_dir=Path("out"),
        )
        bundler = PythonBundler(cfg)
        plan = bundler.build_plan()
        assert any("venv" in step for step in plan)

    def test_build_plan_pyinstaller_has_pyinstaller(self):
        cfg = BundleConfig(
            strategy=BundleStrategy.PYINSTALLER,
            output_dir=Path("out"),
        )
        bundler = PythonBundler(cfg)
        plan = bundler.build_plan()
        assert any("pyinstaller" in step.lower() for step in plan)

    def test_build_plan_nuitka_has_nuitka(self):
        cfg = BundleConfig(
            strategy=BundleStrategy.NUITKA,
            output_dir=Path("out"),
        )
        bundler = PythonBundler(cfg)
        plan = bundler.build_plan()
        assert any("nuitka" in step for step in plan)

    def test_build_plan_conda_pack_has_conda(self):
        cfg = BundleConfig(
            strategy=BundleStrategy.CONDA_PACK,
            output_dir=Path("out"),
        )
        bundler = PythonBundler(cfg)
        plan = bundler.build_plan()
        assert any("conda" in step for step in plan)

    def test_build_plan_system_python_has_comment(self):
        cfg = BundleConfig(
            strategy=BundleStrategy.SYSTEM_PYTHON,
            output_dir=Path("out"),
        )
        bundler = PythonBundler(cfg)
        plan = bundler.build_plan()
        assert any("#" in step for step in plan)

    def test_config_property(self):
        cfg = BundleConfig(app_name="test")
        bundler = PythonBundler(cfg)
        assert bundler.config is cfg
