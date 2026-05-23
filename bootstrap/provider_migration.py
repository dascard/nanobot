"""Provider 配置兼容迁移。"""

from __future__ import annotations

import logging
import os

from config import CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL
from core.database import SessionLocal, SystemSetting
from core.route_metadata import PROVIDER_ALIASES, canonical_provider_id, normalize_base_url


logger = logging.getLogger("nanobot.migration")


def run_provider_migration() -> None:
    """幂等迁移旧 provider key/catalog/route value 到 canonical 名称。"""
    db = SessionLocal()
    migrated: list[str] = []
    try:
        for old_pid, new_pid in PROVIDER_ALIASES.items():
            old_prefix = f"model.providers.{old_pid}."
            new_prefix = f"model.providers.{new_pid}."
            old_rows = db.query(SystemSetting).filter(
                SystemSetting.key.like(f"{old_prefix}%")
            ).all()
            for old_row in old_rows:
                new_key = old_row.key.replace(old_prefix, new_prefix, 1)
                existing = db.query(SystemSetting).filter(
                    SystemSetting.key == new_key
                ).first()
                if existing:
                    continue
                new_row = SystemSetting(
                    key=new_key,
                    value=old_row.value,
                    description=f"{old_row.description or ''} (migrated from {old_pid})",
                )
                db.add(new_row)
                migrated.append(f"{old_row.key} -> {new_key}")
                logger.info("Provider key migrated: %s -> %s", old_row.key, new_key)
        if migrated:
            db.commit()

        route_pv_rows = db.query(SystemSetting).filter(
            SystemSetting.key.like("model.route.%.provider")
        ).all()
        route_migrated = False
        for row in route_pv_rows:
            new_val = canonical_provider_id(row.value or "")
            if new_val != row.value:
                logger.info(
                    "Route provider value migrated: %s = %s -> %s",
                    row.key,
                    row.value,
                    new_val,
                )
                row.value = new_val
                route_migrated = True
        if route_migrated:
            db.commit()

        for old_pid, new_pid in PROVIDER_ALIASES.items():
            old_cat_key = f"model.catalog.{old_pid}"
            new_cat_key = f"model.catalog.{new_pid}"
            old_cat = db.query(SystemSetting).filter(
                SystemSetting.key == old_cat_key
            ).first()
            if not old_cat:
                continue
            existing = db.query(SystemSetting).filter(
                SystemSetting.key == new_cat_key
            ).first()
            if existing:
                continue
            old_cat.key = new_cat_key
            old_cat.description = (old_cat.description or "") + f" (migrated from {old_pid})"
            migrated.append(f"{old_cat_key} -> {new_cat_key}")
            logger.info("Catalog key migrated: %s -> %s", old_cat_key, new_cat_key)
        if migrated:
            db.commit()

        normalized_classifier = normalize_base_url(str(CLASSIFIER_API_URL or ""))
        normalized_vision = normalize_base_url(str(IMAGE_SUMMARY_API_URL or ""))
        if normalized_vision and normalized_vision == normalized_classifier:
            sp_row = db.query(SystemSetting).filter(
                SystemSetting.key == "model.route.sticker_describe.provider"
            ).first()
            current_val = (sp_row.value or "").strip() if sp_row else ""
            if not current_val or current_val in ("vision_qwen", "local_vision"):
                if sp_row:
                    sp_row.value = "local_llama"
                    logger.info(
                        "Merged endpoint: sticker_describe.provider -> local_llama (was %s)",
                        current_val,
                    )
                else:
                    db.add(
                        SystemSetting(
                            key="model.route.sticker_describe.provider",
                            value="local_llama",
                            description="sticker_describe provider (merged endpoint detected)",
                        )
                    )
                    logger.info(
                        "Merged endpoint: created sticker_describe.provider = local_llama"
                    )
                db.commit()

        for pid, env_url, env_key in [
            ("newapi", "NEW_API_BASE_URL", "NEW_API_KEY"),
            ("local_llama", "CLASSIFIER_API_URL", None),
        ]:
            base_key = f"model.providers.{pid}.base_url"
            existing = db.query(SystemSetting).filter(SystemSetting.key == base_key).first()
            if not existing:
                url = os.environ.get(env_url, "")
                if url:
                    db.add(
                        SystemSetting(
                            key=base_key,
                            value=url,
                            description=f"provider {pid} base_url (seeded from env)",
                        )
                    )
                    logger.info("Env seeded: %s = %s", base_key, url[:80])
            if not env_key:
                continue
            api_key = f"model.providers.{pid}.api_key"
            existing_key = db.query(SystemSetting).filter(
                SystemSetting.key == api_key
            ).first()
            if existing_key:
                continue
            key_val = os.environ.get(env_key, "")
            if key_val:
                db.add(
                    SystemSetting(
                        key=api_key,
                        value=key_val,
                        description=f"provider {pid} api_key (seeded from env)",
                    )
                )
                logger.info("Env seeded: %s (value hidden)", api_key)
        db.commit()

        if migrated:
            logger.info("Provider migration summary: %d items", len(migrated))
        else:
            logger.info("Provider migration: nothing to migrate")
    except Exception as exc:
        logger.warning("Provider migration failed (non-fatal): %s", exc)
        db.rollback()
    finally:
        db.close()
        try:
            from core.settings_service import settings

            settings.invalidate()
        except Exception:
            pass
