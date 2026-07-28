from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "scripts/deploy-production-coordinated.sh"
BASE_DEPLOY = ROOT / "scripts/deploy-production.sh"
BACKUP = ROOT / "scripts/sandbox-coordinated-backup.sh"


def test_coordinated_deploy_is_one_root_entry_with_safe_recovery():
    source = COORDINATOR.read_text(encoding="utf-8")

    assert subprocess.run(
        ["bash", "-n", str(COORDINATOR)],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    assert COORDINATOR.stat().st_mode & 0o111
    assert "NANOBOT_DEPLOY_PLAN_ONLY=true" in source
    assert "runtime_deployment_required" in source
    assert "prompt_audit_required" in source
    assert "coordinated_backup_required" in source
    assert "restore_services" in source
    assert "restore_feature_state" in source
    assert "trap on_exit EXIT" in source
    assert "group_learning.enabled" in source
    assert "group_memory.injection_enabled" in source
    assert "NANOBOT_COORDINATED_DEPLOY_STATUS=ok" in source
    assert re.search(r"(^|\n)\s*sudo\s+", source) is None
    assert "prune" not in source
    assert "coordinated_backup_dir" in source
    assert "COORDINATED_BACKUP_REUSED=true" in source
    assert "DEPLOYMENT_RECOVERY_EXECUTED=true" in source
    assert "coordinated-deploy.lock" in source
    assert "flock -n 9" in source


def test_runtime_deploy_supports_fast_plan_and_optional_evidence():
    shell = BASE_DEPLOY.read_text(encoding="utf-8")
    python = (ROOT / "scripts/deploy_release.py").read_text(
        encoding="utf-8"
    )
    deployment = (ROOT / "core/release/deployment.py").read_text(
        encoding="utf-8"
    )

    assert "NANOBOT_DEPLOY_PLAN_ONLY" in shell
    assert "--plan-only" in shell
    assert 'if [[ -n "${backup_dir}" ]]' in shell
    assert 'if [[ -n "${prompt_receipt}" ]]' in shell
    assert "def target_is_current" in deployment
    assert "RUNTIME_DEPLOYMENT_REQUIRED=false" in python
    assert "COORDINATED_BACKUP_REQUIRED=" in python
    assert "PROMPT_AUDIT_REQUIRED=" in python
    assert "schema_migration_head" in python
    assert 'input_hashes.get("prompt_defaults")' in python


def test_backup_gate_checks_fixed_containers_without_compose_reparse():
    source = BACKUP.read_text(encoding="utf-8")

    assert "nanobot-server" in source
    assert "nanobot-session-summary-worker" in source
    assert "nanobot-outbound-delivery-worker" in source
    assert "nanobot-semantic-index-worker" in source
    assert "docker compose ps" not in source
    assert "COORDINATED_BACKUP_DIR=${final_dir}" in source
