from pathlib import Path


def test_mutable_model_registry_state_uses_runtime_data_directory():
    from clients import model_registry
    from core.runtime_paths import RUNTIME_PATHS

    expected_directory = RUNTIME_PATHS.data_dir / "model_registry"

    assert Path(model_registry._FAILURE_STATE_PATH).parent == expected_directory
    assert Path(model_registry._RUNTIME_STATE_PATH).parent == expected_directory
    assert expected_directory != Path(model_registry.__file__).resolve().parent / "data"
