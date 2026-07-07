def test_proactive_outreach_settings_are_registered_as_plain_boolean_switch():
    from core.config_registry import SETTING_DEFS

    expected = {
        "proactive_outreach.enabled": (False, "bool"),
        "proactive_outreach.fallback_interval_min": (120, "int"),
        "proactive_outreach.min_interval_min": (30, "int"),
        "proactive_outreach.max_check_interval_min": (1440, "int"),
        "proactive_outreach.max_silence_min": (2880, "int"),
        "proactive_outreach.surge_min_prob": (0.1, "float"),
        "proactive_outreach.surge_max_prob": (0.6, "float"),
    }

    for key, (default, value_type) in expected.items():
        setting = SETTING_DEFS[key]
        assert setting.key == key
        assert setting.default == default
        assert setting.value_type == value_type
        assert setting.category == "proactive"

    proactive_keys = {key for key in SETTING_DEFS if key.startswith("proactive_outreach.")}
    assert proactive_keys == set(expected)
    assert "proactive_outreach.mode" not in SETTING_DEFS
    assert all("shadow" not in key and "dry" not in key for key in proactive_keys)
