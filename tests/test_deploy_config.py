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


def test_new_api_timeout_defaults_to_300_seconds():
    config_text = Path("config.py").read_text(encoding="utf-8")
    registry_text = Path("core/config_registry.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert 'NEW_API_TIMEOUT = int(os.environ.get("NEW_API_TIMEOUT", "300"))' in config_text
    assert "key=\"new_api.timeout\", env_name=\"NEW_API_TIMEOUT\",\n        default=300" in registry_text
    assert "NEW_API_TIMEOUT=300" in env_example


def test_super_user_ids_use_one_canonical_environment_variable():
    config_text = Path("config.py").read_text(encoding="utf-8")
    registry_text = Path("core/config_registry.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    legacy_reads = (
        'os.environ.get("' + "SUPER" + '_USER_IDS"',
        'os.environ.get("' + "ADMIN" + '_USER_ID"',
    )

    assert 'os.environ.get("NANOBOT_SUPER_USER_IDS", "")' in config_text
    assert "NANOBOT_SUPER_USER_IDS=" in env_example
    assert "bot.super_user_ids" not in registry_text
    assert all(read not in config_text for read in legacy_reads)
    assert "\n" + "SUPER" + "_USER_IDS =" not in config_text
    assert "\n" + "ADMIN" + "_USER_ID =" not in config_text
