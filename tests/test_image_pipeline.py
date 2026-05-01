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
