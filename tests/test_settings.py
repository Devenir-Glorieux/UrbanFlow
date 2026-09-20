from urbanflow.settings import Settings


def test_unified_openrouter_and_local_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    monkeypatch.setenv("LLM_MODEL", "provider/model")
    settings = Settings()
    assert settings.endpoint_for("openai") == "https://openrouter.ai/api/v1"
    assert settings.llm_api_key == "test-secret"
    assert settings.llm_model == "provider/model"
    monkeypatch.setenv("LLM_BASE_URL", "http://host.docker.internal:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    local = Settings()
    assert local.llm_api_key == ""
    assert local.endpoint_for("openai") == "http://host.docker.internal:1234/v1"


def test_provider_specific_fallback_endpoints(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    settings = Settings()
    assert settings.endpoint_for("openai").endswith(":1234/v1")
    assert settings.endpoint_for("ollama").endswith(":11434")
