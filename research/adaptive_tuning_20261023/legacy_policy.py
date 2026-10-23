"""Load the frozen, pre-V2 policy under its own namespace for historical studies."""
import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
directory = root / 'reports/adaptive_tuning_20261023/source_snapshot/src/kis_msj'
if 'adaptive_oct23_policy' not in sys.modules:
    spec = importlib.util.spec_from_file_location('adaptive_oct23_policy', directory/'__init__.py',
                                                submodule_search_locations=[str(directory)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
