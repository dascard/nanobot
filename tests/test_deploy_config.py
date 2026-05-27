from pathlib import Path


def _service_block(compose: str, service_name: str) -> str:
    marker = f"  {service_name}:\n"
    start = compose.index(marker)
    next_service = compose.find("\n  ", start + len(marker))
    while next_service != -1:
        following = compose[next_service + 1 :]
        if following.startswith("  ") and not following.startswith("    "):
            return compose[start:next_service]
        next_service = compose.find("\n  ", next_service + 1)
    return compose[start:]


def test_worker_services_reuse_server_image_without_duplicate_builds():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    server = _service_block(compose, "nanobot-server")
    summary_worker = _service_block(compose, "session-summary-worker")
    semantic_worker = _service_block(compose, "semantic-index-worker")

    assert "build:" in server
    assert "image: nanobot-runtime:latest" in server
    assert "image: nanobot-runtime:latest" in summary_worker
    assert "image: nanobot-runtime:latest" in semantic_worker
    assert "build:" not in summary_worker
    assert "build:" not in semantic_worker


def test_dockerignore_excludes_runtime_model_directories():
    ignored = {
        line.strip()
        for line in Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert "models/" in ignored
    assert "sentinel/" in ignored
