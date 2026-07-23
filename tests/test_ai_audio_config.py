"""Env configurability of the DeepSeek audio-tagging endpoint/model."""

import importlib

import server.ai_audio as ai_audio


def test_defaults(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    importlib.reload(ai_audio)
    assert ai_audio.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert ai_audio.DEEPSEEK_MODEL == "deepseek-v4-flash"
    importlib.reload(ai_audio)


def test_env_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.siliconflow.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
    importlib.reload(ai_audio)
    assert ai_audio.DEEPSEEK_BASE_URL == "https://api.siliconflow.com/v1"
    assert ai_audio.DEEPSEEK_MODEL == "deepseek-ai/DeepSeek-V4-Flash"
    importlib.reload(ai_audio)
