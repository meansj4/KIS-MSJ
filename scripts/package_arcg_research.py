"""Create a self-contained review package from an explicit safe file allowlist."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package(output: Path):
    output = output.resolve()
    if output.parent != ROOT.resolve():
        raise ValueError("The output must be an immediate directory of this workspace")
    source = output / "source"
    safe_sources = sorted((ROOT / "src/kis_msj").glob("*.py"))
    new_code = sorted((ROOT / "src/kis_msj").glob("arcg*.py"))
    new_tests = sorted((ROOT / "tests").glob("test_arcg*.py"))
    scripts = [ROOT / "scripts" / name for name in (
        "run_arcg_research.py", "report_arcg_research.py", "package_arcg_research.py", "arcg_pytest_isolation.py")]
    for path in safe_sources + new_tests + scripts + [ROOT / "pyproject.toml"]:
        destination = source / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    # Include exact current local source, since the user's earlier dirty changes
    # are intentionally neither discarded nor silently folded into our commit.
    patch = []
    for path in new_code + new_tests + scripts:
        name = path.relative_to(ROOT).as_posix()
        patch.append(f"diff --git a/{name} b/{name}\nnew file mode 100644\n")
        patch.extend(difflib.unified_diff([], path.read_text(encoding="utf-8").splitlines(keepends=True),
                                         fromfile="/dev/null", tofile=f"b/{name}"))
    (output / "arcg_changes.patch").write_text("".join(patch), encoding="utf-8", newline="\n")
    original = json.loads((ROOT / "tmp/arcg_before/source_sha256.json").read_text(encoding="utf-8"))
    unchanged = {name: sha(ROOT / name) == digest for name,digest in original.items()}
    if not all(unchanged.values()):
        raise RuntimeError("A preserved original file changed: " + ", ".join(n for n,ok in unchanged.items() if not ok))
    git = lambda *args: subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()
    (output / "git_metadata.json").write_text(json.dumps({
        "branch": git("branch", "--show-current"), "commit": git("rev-parse", "HEAD"),
        "original_commit": "490ef3e", "preserved_original_files_unchanged": unchanged,
        "preexisting_dirty_changes": "Preserved and not included in the ARCG commit; source snapshot is the replay reference.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    required = ["01_strategy_spec.md", "02_code_change_report.md", "03_backtest_comparison.csv",
                "04_optimization_results.csv", "05_equity_curve.csv", "06_trade_history.csv",
                "07_parameter_recommendation.md", "08_risk_analysis.md", "09_final_research_report.md",
                "equity_comparison.png", "drawdown_comparison.png", "cash_ratio_comparison.png",
                "monthly_returns.png", "parameter_sensitivity.png", "market_regime_performance.png"]
    missing = [name for name in required if not (output/name).is_file()]
    if missing:
        raise RuntimeError("Missing required artifacts: " + ", ".join(missing))
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.suffix.lower() != ".zip"
                   and p.name not in {"package_manifest.json", "legacy_settings.sanitized.json"}
                   and "__pycache__" not in p.parts)
    manifest = {p.relative_to(output).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size} for p in files}
    manifest_path = output / "package_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    files.append(manifest_path)
    zip_path = ROOT / "KIS_MSJ_ARCG_Research_Package.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for path in files:
            archive.write(path, "analysis_output/" + path.relative_to(output).as_posix())
    with zipfile.ZipFile(zip_path) as archive:
        error = archive.testzip()
        if error:
            raise RuntimeError("Corrupt ZIP member: " + error)
        for path in files:
            name = "analysis_output/" + path.relative_to(output).as_posix()
            if hashlib.sha256(archive.read(name)).hexdigest() != sha(path):
                raise RuntimeError("Packaged content hash mismatch: " + name)
    print(json.dumps({"zip": str(zip_path), "bytes": zip_path.stat().st_size, "members": len(files),
                      "sha256": sha(zip_path), "original_files_unchanged": all(unchanged.values())}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "analysis_output")
    package(parser.parse_args().output)
