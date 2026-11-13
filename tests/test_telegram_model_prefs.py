import json
from pathlib import Path
from unittest.mock import patch

import telegram_service


def test_telegram_prefs_and_resolution(tmp_path):
    prefs_file = tmp_path / "telegram_prefs.json"
    with patch.object(telegram_service, "TELEGRAM_PREFS_PATH", prefs_file):
        # 預設為 hydrogen + auto
        prefs = telegram_service.get_telegram_prefs()
        assert prefs == {"model_family": "hydrogen", "thinking_level": "auto"}

        # 儲存為 argon + high
        telegram_service.save_telegram_prefs({"model_family": "argon", "thinking_level": "high"})
        assert json.loads(prefs_file.read_text(encoding="utf-8")) == {
            "model_family": "argon",
            "thinking_level": "high",
        }

        level_arg, model_id, label = telegram_service.resolve_telegram_model_and_level("你好")
        assert level_arg == "high"
        assert model_id == "gemini-4-argon-high"
        assert "Gemini 4 Argon" in label

        # 測試不支援 xhigh 的模型平滑回退至 high
        telegram_service.save_telegram_prefs({"model_family": "flash", "thinking_level": "xhigh"})
        level_arg, model_id, _ = telegram_service.resolve_telegram_model_and_level("你好")
        assert level_arg == "xhigh"
        assert model_id == "gemini-3.8-flash-high"

        # 測試 build_model_settings_card
        telegram_service.save_telegram_prefs({"model_family": "hydrogen", "thinking_level": "xhigh"})
        text, markup = telegram_service.build_model_settings_card()
        assert "Gemini 4 Hydrogen" in text
        assert "gemini-4-hydrogen-xhigh" in text
        assert "inline_keyboard" in markup
        assert len(markup["inline_keyboard"]) == 4
