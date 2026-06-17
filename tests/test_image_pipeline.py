import base64
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image


def _make_noisy_png_bytes(size=(256, 256)) -> bytes:
    raw = bytes((i % 251 for i in range(size[0] * size[1] * 3)))
    img = Image.frombytes("RGB", size, raw)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_downloads_compresses_and_caches(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_MAX_BYTES", 20_000)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_MAX_SIDE", 64)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_START_QUALITY", 90)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_MIN_QUALITY", 40)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT", 1.0)

    payload = _make_noisy_png_bytes()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.headers.get_content_type.return_value = "image/png"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        prepared1 = pipeline.prepare_image("https://example.com/noisy.png")
        prepared2 = pipeline.prepare_image("https://example.com/noisy.png")

    assert prepared1.cache_path == prepared2.cache_path
    assert Path(prepared1.cache_path).exists()
    assert prepared1.data_url.startswith("data:image/jpeg;base64,")
    raw_decoded = base64.b64decode(prepared1.data_url.split(",", 1)[1])
    assert len(raw_decoded) <= 20_000
    assert mock_urlopen.call_count == 1


def test_prepare_image_rejects_local_file_by_default(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    image_path = tmp_path / "local.png"
    image_path.write_bytes(_make_noisy_png_bytes((16, 16)))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_ALLOW_LOCAL_FILES", False)

    try:
        pipeline.prepare_image(str(image_path))
    except ValueError as exc:
        assert "本地文件" in str(exc)
    else:
        raise AssertionError("应拒绝默认读取本地图片路径")


def test_prepare_image_rejects_local_file_outside_allowed_roots(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(_make_noisy_png_bytes((16, 16)))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_ALLOW_LOCAL_FILES", True)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_LOCAL_FILE_ROOTS", str(allowed_dir), raising=False)

    try:
        pipeline.prepare_image(outside_path.as_uri())
    except ValueError as exc:
        assert "允许目录" in str(exc)
    else:
        raise AssertionError("应拒绝读取白名单目录外的本地图片")


def test_prepare_image_allows_local_file_inside_allowed_roots(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    image_path = allowed_dir / "local.png"
    image_path.write_bytes(_make_noisy_png_bytes((16, 16)))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_ALLOW_LOCAL_FILES", True)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_LOCAL_FILE_ROOTS", str(allowed_dir))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_CACHE_DIR", str(tmp_path / "cache"))

    prepared = pipeline.prepare_image(str(image_path))

    assert Path(prepared.cache_path).exists()
    assert prepared.data_url.startswith("data:image/jpeg;base64,")


def test_prepare_image_rejects_oversized_download(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_RAW_MAX_BYTES", 10)
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT", 1.0)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"x" * 11
    mock_resp.headers.get_content_type.return_value = "image/png"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        try:
            pipeline.prepare_image("https://example.com/too-large.png")
        except ValueError as exc:
            assert "过大" in str(exc)
        else:
            raise AssertionError("应拒绝超过原始下载上限的图片")


def test_prepare_image_reports_qq_expired_download_body(tmp_path, monkeypatch):
    import urllib.error

    import nanobot_kt.image_pipeline as pipeline

    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_DOWNLOAD_TIMEOUT", 1.0)

    qq_url = "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=abc&rkey=expired"
    body = b'{"retcode":-5503007,"retmsg":"download url has expired","retryflag":0}'
    err = urllib.error.HTTPError(
        qq_url,
        400,
        "Bad Request",
        {"Content-Type": "application/json", "X-ErrNo": "-5503007"},
        io.BytesIO(body),
    )
    mock_opener = MagicMock()
    mock_opener.open.side_effect = err

    with patch("urllib.request.build_opener", return_value=mock_opener):
        try:
            pipeline.prepare_image(qq_url)
        except ValueError as exc:
            msg = str(exc)
            assert "QQ图片链接已过期" in msg
            assert "-5503007" in msg
        else:
            raise AssertionError("应把 QQ 过期响应转换为明确错误")


def test_prepare_image_rejects_placeholder_qq_source(tmp_path, monkeypatch):
    import nanobot_kt.image_pipeline as pipeline

    monkeypatch.setattr(pipeline, "IMAGE_PREPROCESS_CACHE_DIR", str(tmp_path))

    try:
        pipeline.prepare_image(
            "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=xxx&spec=0&r=xxx&rr=xxx"
        )
    except ValueError as exc:
        assert "占位符" in str(exc)
    else:
        raise AssertionError("应拒绝已经脱敏的 QQ 图片占位 URL")
