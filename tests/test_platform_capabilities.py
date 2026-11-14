from easel.platform_capabilities import project_capabilities


def test_capabilities_are_read_only_projection():
    capabilities = project_capabilities(
        {
            "xiaohongshu": {"backend": "xhs"},
            "wechat-oa": {"backend": "wechat-oa"},
            "disabled": {"backend": "unsupported"},
        },
        {"xiaohongshu"},
    )
    assert capabilities["xiaohongshu"].media_constraints.requires_media is True
    assert capabilities["xiaohongshu"].analytics_scope == "account"
    assert capabilities["wechat-oa"].auth_mode == "credentials"
    assert capabilities["disabled"].publish_mode == "none"
    assert "xiaohongshu" in capabilities
