"""设置值范围校验回归测试。"""

from core.config_registry import SettingDef
from core.settings_service import SettingsService


def test_cast_out_of_range_numeric_value_falls_back_to_default():
    service = SettingsService()
    temperature = SettingDef(
        key="model.route.session_summary.temperature",
        env_name="",
        default=0.1,
        value_type="float",
        category="model",
        min_value=0,
        max_value=2,
    )
    max_tokens = SettingDef(
        key="model.route.session_summary.max_tokens",
        env_name="",
        default=1200,
        value_type="int",
        category="model",
        min_value=64,
        max_value=8000,
    )

    assert service._cast("3.0", temperature) == 0.1
    assert service._cast("65535", max_tokens) == 1200
