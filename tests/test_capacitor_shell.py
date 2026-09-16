"""Capacitor shell scaffolding checks."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "apps" / "capacitor-shell"


def test_capacitor_load_config_writes_server_url():
    script = SHELL / "scripts" / "load-config.js"
    assert script.is_file()
    env_example = SHELL / ".env.example"
    assert "CAP_SERVER_URL" in env_example.read_text()
    subprocess.run(
        ["node", str(script)],
        cwd=SHELL,
        check=True,
        capture_output=True,
        text=True,
    )
    cfg_path = SHELL / "capacitor.config.json"
    assert cfg_path.is_file()
    cfg = json.loads(cfg_path.read_text())
    assert cfg["webDir"] == "www"
    assert "url" in cfg["server"]
    assert cfg["appId"] == "com.inertia.investment"


def test_capacitor_www_fallback_exists():
    index = SHELL / "www" / "index.html"
    assert index.is_file()
    assert "INERTIA" in index.read_text()


def test_android_platform_scaffolded():
    manifest = SHELL / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    assert manifest.is_file()
    text = manifest.read_text()
    assert "INTERNET" in text
    assert "usesCleartextTraffic" in text
