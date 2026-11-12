"""Load parser implementations from colocated Python files."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module_from_path(module_path: str | Path, *, module_namespace: str) -> ModuleType:
    """Load and cache a Python module under a path-unique module name."""
    path = Path(module_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Plugin Python file does not exist: {path}")

    path_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    package_hash = hashlib.sha256(str(path.parent).encode("utf-8")).hexdigest()[:16]
    package_name = f"_{module_namespace}_{package_hash}"
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(path.parent)]
        sys.modules[package_name] = package
    module_name = f"{package_name}.{path.stem}_{path_hash}"
    module = sys.modules.get(module_name)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_plugin_class(
    module_path: str | Path,
    *,
    class_name: str,
    base_class: type,
    module_namespace: str,
) -> type:
    path = Path(module_path).resolve()
    module = load_module_from_path(path, module_namespace=module_namespace)

    plugin_class = getattr(module, class_name, None)
    if (
        plugin_class is None
        or not isinstance(plugin_class, type)
        or not issubclass(plugin_class, base_class)
    ):
        raise TypeError(f"{path} must define {class_name}({base_class.__name__}).")
    return plugin_class
