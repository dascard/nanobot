"""Sticker suite runner——expand、send_code、image endpoint、duplicate canonical。"""
from __future__ import annotations

import os
import tempfile
import shutil
from evals.schema import EvalCase, EvalOutput


def run_sticker_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    # 创建临时 cache dir，monkeypatch _cache_dir 使用它
    tmp_cache_dir = tempfile.mkdtemp(prefix="sticker_cache_eval_")
    import core.sticker_preview as sp
    orig_cache_dir = sp._cache_dir
    sp._cache_dir = lambda: tmp_cache_dir

    tmp_dirs_created = [tmp_cache_dir]

    try:
        # sticker public proxy: 测试 _canonical_row_send_code
        if "content" in inp and inp.get("content", "").startswith("[sticker:"):
            import re
            from core.sticker_memory import expand_sticker_refs_in_content
            from core.database import StickerMemory, get_db, Base
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            sticker_id = inp.get("sticker_id", 0)
            public_base_url = inp.get("public_base_url", "http://127.0.0.1:8000")
            os.environ["NANOBOT_PUBLIC_BASE_URL"] = public_base_url

            db_path = os.path.join(tempfile.mkdtemp(), "sticker_eval.db")
            tmp_dirs_created.append(os.path.dirname(db_path))
            engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)
            db = Session()

            try:
                row = StickerMemory(
                    id=sticker_id,
                    file_ref="https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=test",
                    sticker_hash="eval-hash",
                    description="test sticker",
                    tags_json="[]",
                    emotions_json="[]",
                    status="active",
                    preview_status="ok",
                    chat_stream_id="qq:123:group",
                )
                db.add(row)
                db.commit()

                result = expand_sticker_refs_in_content(inp["content"], db)
                out.raw["expanded_content"] = result
            finally:
                db.close()

        # sticker image endpoint
        elif inp.get("method") == "GET" and "/stickers/" in inp.get("path", ""):
            from fastapi.testclient import TestClient
            from core.database import StickerMemory, Base, get_db
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            from server import app
            db_path = os.path.join(tempfile.mkdtemp(), "sticker_eval2.db")
            tmp_dirs_created.append(os.path.dirname(db_path))
            engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)

            def override_get_db():
                db = Session()
                try:
                    yield db
                finally:
                    db.close()

            app.dependency_overrides[get_db] = override_get_db
            os.environ.setdefault("NANOBOT_STICKER_IMAGE_TOKEN", "")

            try:
                db = Session()
                sticker_id = inp.get("sticker_id", 0)
                duplicate_of_id = inp.get("duplicate_of_id", 0)

                local_path = os.path.join(tmp_cache_dir, f"eval_sticker_{sticker_id}.png")
                with open(local_path, "wb") as f:
                    f.write(b"fake-png-content-for-eval")

                row = StickerMemory(
                    id=sticker_id,
                    file_ref="https://example.com/test.png",
                    sticker_hash="eval-endpoint-hash",
                    status="active",
                    preview_status="ok",
                    local_path=local_path,
                    chat_stream_id="qq:123:group",
                )
                if duplicate_of_id:
                    row.duplicate_of_id = duplicate_of_id
                    row.status = "disabled"
                    canon_path = os.path.join(tmp_cache_dir, f"eval_sticker_canon_{duplicate_of_id}.png")
                    with open(canon_path, "wb") as f:
                        f.write(b"canon-png-content-for-eval")
                    canonical = StickerMemory(
                        id=duplicate_of_id,
                        file_ref="https://example.com/canon.png",
                        sticker_hash="canon-hash",
                        status="active",
                        preview_status="ok",
                        local_path=canon_path,
                        chat_stream_id="qq:123:group",
                    )
                    db.add(canonical)
                db.add(row)
                db.commit()
                db.close()

                client = TestClient(app)
                path = inp["path"]
                if "?" in path:
                    path = path.split("?")[0]
                resp = client.get(path)
                out.http_status = resp.status_code
                out.content_type = resp.headers.get("content-type", "")
                out.raw["response_body_len"] = len(resp.content)
            finally:
                app.dependency_overrides.clear()

        # duplicate canonical — 已在上面分支处理
        else:
            # generic sticker check
            from core.sticker_memory import _canonical_row_send_code, _public_sticker_image_url
            out.raw["checked"] = True
    finally:
        sp._cache_dir = orig_cache_dir
        # 清理临时文件
        for d in tmp_dirs_created:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)

    return out
