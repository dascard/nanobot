from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sandbox_image_is_independent_pinned_and_non_root():
    dockerfile = (ROOT / "docker/sandbox/python/Dockerfile").read_text()

    assert "python:3.11.13-slim-bookworm@sha256:" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "COPY requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "COPY . ." not in dockerfile
    assert "nanobot-runtime" not in dockerfile
    assert "Dockerfile" not in dockerfile.split("COPY requirements.lock", 1)[0]
    assert ":latest" not in dockerfile


def test_sandbox_build_context_contains_no_server_source_or_secrets():
    context = ROOT / "docker/sandbox/python"
    names = {
        path.relative_to(context).as_posix()
        for path in context.rglob("*")
        if path.is_file()
    }

    assert names == {"Dockerfile", "requirements.in", "requirements.lock"}


def test_sandbox_build_script_has_security_smoke_and_no_global_cleanup():
    script = (ROOT / "scripts/build-sandbox-image.sh").read_text()

    assert 'version}" == "latest"' in script
    assert "--network none" in script
    assert "--read-only" in script
    assert "--cap-drop ALL" in script
    assert "--security-opt no-new-privileges" in script
    assert "--pids-limit 128" in script
    assert "--memory 512m" in script
    assert "docker system prune" not in script
    assert "docker image prune" not in script
    assert "docker volume prune" not in script
