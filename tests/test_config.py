from datetime import time

from workreport import credentials
from workreport.config import CategoryRule, Settings, load_settings, save_settings


def test_defaults_when_missing(tmp_path):
    s = load_settings(tmp_path / "none.json")
    assert s.claude_model == "claude-opus-5-5"
    assert s.screenshot_enabled is False
    assert s.stt_engine == "whisper_local"


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    s = Settings(user_name="홍길동", work_end="19:30", category_rules=[CategoryRule(pattern="jira", category="이슈관리")])
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.user_name == "홍길동"
    assert loaded.work_end_time() == time(19, 30)
    assert loaded.category_rules[0].category == "이슈관리"


def test_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_bad_time_uses_default():
    assert Settings(work_start="nope").work_start_time() == time(9, 0)


def test_credentials_roundtrip():
    credentials.set_secret(credentials.ANTHROPIC_API_KEY, "sk-test")
    assert credentials.get_secret(credentials.ANTHROPIC_API_KEY) == "sk-test"
    credentials.set_secret(credentials.ANTHROPIC_API_KEY, "")
    assert credentials.get_secret(credentials.ANTHROPIC_API_KEY) == ""
