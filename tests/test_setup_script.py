"""Safe setup preparation checks without Docker, credentials or network calls."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("bash") is None, reason="setup.sh targets Bash")
def test_prepare_fresh_checkout_preserves_existing_configuration(tmp_path):
    for relative in ("setup.sh", ".env.example", "backend/.env.example", "frontend/.env.example"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    subprocess.run(["bash", "-n", str(tmp_path / "setup.sh")], check=True)
    subprocess.run(["bash", str(tmp_path / "setup.sh"), "--prepare"], check=True, cwd=tmp_path.parent)
    assert (tmp_path / ".env").exists()
    backend = tmp_path / "backend/.env"
    assert "@127.0.0.1:5432/live_swot" in backend.read_text()
    frontend = tmp_path / "frontend/.env.local"
    assert "BACKEND_API_URL=http://localhost:8000" in frontend.read_text()
    assert "NEXT_PUBLIC_USE_MOCKS=false" in frontend.read_text()
    original = backend.read_text() + "\n# local edit\n"
    backend.write_text(original)
    subprocess.run(["bash", str(tmp_path / "setup.sh"), "--prepare"], check=True)
    assert backend.read_text() == original
    if os.name == "posix":
        assert not (frontend.stat().st_mode & 0o077)


def test_prepare_custom_database_port(tmp_path):
    for relative in ("setup.sh", ".env.example", "backend/.env.example", "frontend/.env.example"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)

    subprocess.run(
        ["bash", str(tmp_path / "setup.sh"), "--prepare"],
        check=True,
        env={**os.environ, "SCADABLE_DB_PORT": "5436"},
    )

    assert "@127.0.0.1:5436/live_swot" in (
        tmp_path / "backend/.env"
    ).read_text()
