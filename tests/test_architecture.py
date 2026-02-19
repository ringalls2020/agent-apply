from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_service_layers_use_package_layout() -> None:
    assert not (ROOT / "backend/services.py").exists()
    assert not (ROOT / "cloud_automation/services.py").exists()
    assert (ROOT / "backend/services/__init__.py").exists()
    assert (ROOT / "cloud_automation/services/__init__.py").exists()


def test_entrypoints_do_not_import_legacy_service_modules() -> None:
    backend_main = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    cloud_main = (ROOT / "cloud_automation/main.py").read_text(encoding="utf-8")
    assert "services_legacy" not in backend_main
    assert "services_legacy" not in cloud_main


def test_service_modules_do_not_import_legacy_modules() -> None:
    service_roots = [
        ROOT / "backend/services",
        ROOT / "cloud_automation/services",
    ]
    for service_root in service_roots:
        for module_path in service_root.rglob("*.py"):
            contents = module_path.read_text(encoding="utf-8")
            assert "services_legacy" not in contents, str(module_path)
