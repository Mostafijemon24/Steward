"""OpenRouter free-model provider (no database)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.engine.providers import (
    ModelError,
    ModelMessage,
    ModelRequest,
    OpenRouterProvider,
    clear_openrouter_free_model_cache,
    get_provider,
    is_openrouter_free_model,
)


def _dev_settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://steward_app:unused@127.0.0.1:5432/unused",
        approval_signing_key="dev-only-approval-signing-key-change-me",
        **kw,
    )


def _header(headers: Any, *names: str) -> str | None:
    for name in names:
        value = headers.get(name)
        if value:
            return value
    return None


class _FakeOpenRouter:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.queue: list[tuple[int, dict[str, Any] | None]] = []

    def start(self) -> str:
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "method": "GET",
                        "referer": _header(self.headers, "HTTP-Referer", "Http-Referer", "Referer"),
                        "title": self.headers.get("X-Title"),
                    }
                )
                data = json.dumps(
                    {
                        "data": [
                            {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
                            {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"}},
                            {"id": "nvidia/nemotron-3.5-lightning:free", "pricing": {"prompt": "0", "completion": "0"}},
                            {
                                "id": "nvidia/nemotron-3.5-content-safety:free",
                                "pricing": {"prompt": "0", "completion": "0"},
                            },
                            {"id": "openai/gpt-4o-mini", "pricing": {"prompt": "0.0001", "completion": "0.0002"}},
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "method": "POST",
                        "body": body,
                        "referer": _header(self.headers, "HTTP-Referer", "Http-Referer", "Referer"),
                        "title": self.headers.get("X-Title"),
                    }
                )
                status, payload = fake.queue.pop(0) if fake.queue else (200, None)
                data = json.dumps(
                    payload
                    or {
                        "model": "google/gemma-4-31b-it:free",
                        "choices": [{"message": {"content": '{"summary":"ok","steps":[]}'}}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
                    }
                ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


def test_is_openrouter_free_model() -> None:
    assert is_openrouter_free_model("openrouter/free")
    assert is_openrouter_free_model("google/gemma-4-31b-it:free")
    assert not is_openrouter_free_model("openai/gpt-4o-mini")


def test_openrouter_free_provider_uses_router_and_auto_fallbacks() -> None:
    clear_openrouter_free_model_cache()
    fake = _FakeOpenRouter()
    base = fake.start()
    s = _dev_settings(
        model_provider="openrouter",
        model_base_url=base,
        model_name="",
        model_api_key=SecretStr("or-test-key"),
        model_free_only=True,
        model_fallbacks="auto",
        model_location="local",
        model_provider_label="OpenRouter",
        model_site_url="https://steward.example",
        model_app_name="Steward",
        app_origin="https://steward.example",
    )
    p = OpenRouterProvider(s)
    assert p.info.configured and p.info.key == "openrouter"
    assert p.info.model == "openrouter/free"
    assert p.info.location == "external"
    out = p.generate(
        ModelRequest(
            purpose="plan",
            messages=[ModelMessage("user", "plan a task")],
            max_output_tokens=200,
            json_output=True,
        ),
        timeout=5.0,
    )
    assert out.text.startswith("{") and out.model == "google/gemma-4-31b-it:free"
    post = next(r for r in fake.requests if r.get("method") == "POST")
    assert post["auth"] == "Bearer or-test-key"
    assert post["body"]["model"] == "openrouter/free"
    assert post["body"]["response_format"] == {"type": "json_object"}
    assert "google/gemma-4-31b-it:free" in post["body"]["models"]
    assert "nvidia/nemotron-3.5-lightning:free" in post["body"]["models"]
    assert "openai/gpt-4o-mini" not in post["body"]["models"]
    assert "content-safety" not in str(post["body"]["models"])
    assert post["title"] == "Steward"
    assert post["referer"] == "https://steward.example"
    fake.srv.shutdown()


def test_openrouter_free_only_rejects_paid_model() -> None:
    s = _dev_settings(
        model_provider="openrouter",
        model_base_url="https://openrouter.ai/api/v1",
        model_name="openai/gpt-4o-mini",
        model_api_key=SecretStr("or-test-key"),
        model_free_only=True,
        model_fallbacks="",
    )
    with pytest.raises(ModelError, match="MODEL_FREE_ONLY"):
        OpenRouterProvider(s)
    assert get_provider(s).info.configured is False


def test_get_provider_selects_openrouter() -> None:
    s = _dev_settings(
        model_provider="openrouter",
        model_base_url="https://openrouter.ai/api/v1",
        model_name="openrouter/free",
        model_api_key=SecretStr("or-test-key"),
        model_free_only=True,
        model_fallbacks="",
    )
    p = get_provider(s)
    assert isinstance(p, OpenRouterProvider) and p.info.model == "openrouter/free"


def test_openrouter_production_requires_api_key_and_free_ids() -> None:
    base = dict(
        env="production",
        database_url="postgresql+psycopg://steward_app:Str0ng-random-pw@db/steward",
        approval_signing_key="x" * 40,
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        app_origin="https://app.example.com",
        trust_proxy_headers=True,
        secrets_encryption_key="cmFuZG9tLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
        integration_secrets_public_key="cHVibGljLWtleS1mb3ItdGVzdHMtMzItYnl0ZXMhISE=",
        model_provider="openrouter",
        model_free_only=True,
    )
    # API process may omit the key (compose clears it); workers must have it.
    Settings(_env_file=None, **base, process_role="api", model_name="openrouter/free")
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        Settings(_env_file=None, **base, process_role="worker", model_name="openrouter/free")
    with pytest.raises(ValueError, match="MODEL_FREE_ONLY"):
        Settings(
            _env_file=None,
            **base,
            process_role="api",
            model_api_key=SecretStr("or-prod-key"),
            model_name="openai/gpt-4o-mini",
        )
    Settings(
        _env_file=None,
        **base,
        process_role="worker",
        model_api_key=SecretStr("or-prod-key"),
        model_name="openrouter/free",
        model_fallbacks="auto",
    )
