"""表情预览后台任务。"""

import logging

logger = logging.getLogger("nanobot.sticker_preview")


def cache_sticker_preview_bg(sticker_id: int) -> None:
    from core.database import StickerMemory
    from core.sticker_preview import cache_sticker_preview
    from core.uow import UnitOfWork

    with UnitOfWork() as uow:
        row = uow.db.query(StickerMemory).filter(StickerMemory.id == sticker_id).first()
        force = row is not None and row.preview_status in {
            "expired",
            "fetch_failed",
            "invalid_image",
            "invalid_ref",
        }
        result = cache_sticker_preview(uow.db, sticker_id, force=force)
        logger.info(
            "[StickerPreview] bg cache id=%s ok=%s status=%s force=%s err=%s",
            sticker_id,
            result.ok,
            result.status,
            force,
            result.error[:120],
        )
