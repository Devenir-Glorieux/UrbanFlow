import asyncio
import json

import httpx
import pytest

from urbanflow.analytics.baseline import GravityBaseline
from urbanflow.behavior.providers import ProviderConfig, StructuredProvider
from urbanflow.common import DomainError


class MemoryCache:
    def __init__(self):
        self.entries = {}
        self.metadata = {}

    async def get(self, key):
        return self.entries.get(key)

    async def put(self, key, request, plan, metadata):
        self.entries.setdefault(key, plan)
        self.metadata[key] = {"request": request, "metadata": metadata}
        return self.entries[key]


def provider_json(plan):
    return json.dumps(
        {
            "visits": [activity.model_dump() for activity in plan.activities[1:-1]],
            "return_home_minute": plan.activities[-1].minute,
        }
    )


def provider_response(provider, content, finish_reason="stop", refusal=None):
    message = {"role": "assistant", "content": content, "refusal": refusal}
    if provider == "ollama":
        return httpx.Response(200, json={"message": message, "done_reason": finish_reason})
    return httpx.Response(
        200,
        json={
            "id": "response-test",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
        },
    )


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_provider_contract_and_cache(context, provider):
    plan = await GravityBaseline().plan(context)
    calls = []

    async def handle(request):
        data = json.loads(request.content)
        calls.append(data)
        schema = (
            data["response_format"]["json_schema"]["schema"]
            if provider == "openai"
            else data["format"]
        )
        if provider == "openai":
            assert data["max_completion_tokens"] == 8192
            assert "temperature" not in data
            assert request.headers["authorization"] == "Bearer secret"
            assert data["response_format"]["json_schema"]["strict"] is True
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["PlannedVisit"]["additionalProperties"] is False
        assert set(schema["required"]) == {"visits", "return_home_minute"}
        destination = schema["$defs"]["PlannedVisit"]["properties"]["destination_id"]
        assert destination["enum"] == [
            candidate.id
            for candidate in context.candidates
            if candidate.id != context.agent.home_id
        ]
        message = {"content": provider_json(plan)}
        return httpx.Response(
            200,
            json={"choices": [{"message": message}]}
            if provider == "openai"
            else {"message": message},
        )

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test-model"),
            "http://provider.test/v1",
            "secret",
            client,
            cache,
        )
        assert await engine.plan(context) == plan
        assert await engine.plan(context) == plan
        assert len(calls) == 1
        assert "secret" not in json.dumps(cache.metadata)
        engine.config = engine.config.model_copy(update={"model": "another-model"})
        await engine.plan(context)
        assert len(calls) == 2


async def test_openai_compatible_structured_content_object(context):
    plan = await GravityBaseline().plan(context)
    message = {"content": json.loads(provider_json(plan))}

    async def handle(request):
        return httpx.Response(
            200,
            json={
                "id": "response-id",
                "choices": [{"finish_reason": "stop", "message": message}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider="openai", model="test-model"),
            "http://provider.test/v1",
            "",
            client,
            MemoryCache(),
        )
        assert await engine.plan(context) == plan


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_invalid_output_retried_then_rejected(context, monkeypatch, provider):
    calls = 0

    async def no_wait(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    async def handle(request):
        nonlocal calls
        calls += 1
        return provider_response(provider, '{"activities": []}')

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test", retries=2),
            "http://provider.test",
            "",
            client,
            cache,
        )
        with pytest.raises(DomainError, match="3 attempts: JSON did not match"):
            await engine.plan(context)
    assert calls == 3 and not cache.entries


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_domain_validation_feedback_is_sent_on_retry(context, monkeypatch, provider):
    valid = await GravityBaseline().plan(context)
    invalid = json.dumps(
        {
            "visits": [{"destination_id": "outside", "purpose": "other", "minute": 600}],
            "return_home_minute": 1200,
        }
    )
    requests = []

    async def no_wait(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)

    async def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        content = invalid if len(requests) == 1 else provider_json(valid)
        return provider_response(provider, content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test", retries=1),
            "http://provider.test",
            "",
            client,
            MemoryCache(),
        )
        assert await engine.plan(context) == valid

    assert len(requests) == 2
    feedback = requests[1]["messages"][-1]["content"]
    assert "outside the supplied candidates" in feedback
    assert "Allowed non-home" in feedback


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_concurrency_bound(context, provider):
    plan = await GravityBaseline().plan(context)
    active = peak = 0

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return provider_response(provider, provider_json(plan))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test", concurrency=2),
            "http://provider.test",
            "",
            client,
            MemoryCache(),
        )
        await asyncio.gather(
            *(engine.plan(context.model_copy(update={"seed": i})) for i in range(7))
        )
    assert peak == 2


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_timeout_is_bounded(context, provider):
    async def handle(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test", retries=0, timeout_seconds=0.01),
            "http://provider.test",
            "",
            client,
            MemoryCache(),
        )
        with pytest.raises(DomainError, match="timed out"):
            await engine.plan(context)


async def test_openrouter_requires_schema_support_and_local_key_is_optional(context):
    plan = await GravityBaseline().plan(context)
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        seen.append(payload)
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"choices": [{"message": {"content": provider_json(plan)}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for endpoint in ("https://openrouter.ai/api/v1", "http://localhost:1234/v1"):
            provider = StructuredProvider(
                ProviderConfig(provider="openai", model="test"), endpoint, "", client, MemoryCache()
            )
            await provider.plan(context)
    assert seen[0]["provider"] == {"require_parameters": True}
    assert "provider" not in seen[1]


@pytest.fixture
def no_retry_delay(monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_wait)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("truncated_content", [None, "partial", "valid"])
async def test_length_retries_with_larger_budget(
    context, provider, truncated_content, no_retry_delay
):
    plan = await GravityBaseline().plan(context)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            content = provider_json(plan) if truncated_content == "valid" else truncated_content
            return provider_response(provider, content, "length")
        return provider_response(provider, provider_json(plan))

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider=provider, model="test"),
            "http://provider.test/v1",
            "",
            client,
            cache,
        )
        assert await engine.plan(context) == plan
        assert await engine.plan(context) == plan
    assert len(calls) == 2
    budgets = [
        call["max_completion_tokens"] if provider == "openai" else call["options"]["num_predict"]
        for call in calls
    ]
    assert budgets == [8192, 16384]
    assert calls[0]["messages"] == calls[1]["messages"]  # Never feed partial JSON back.
    metadata = next(iter(cache.metadata.values()))["metadata"]
    assert metadata["attempts"] == 2
    assert metadata["max_completion_tokens"] == 16384
    assert metadata["provider_plan"] == json.loads(provider_json(plan))
    if provider == "openai":
        assert metadata["usage"]["total_tokens"] == 140
        assert metadata["response_id"] == "response-test"


@pytest.mark.parametrize(
    "token_limit, budgets", [(32768, [8192, 16384, 32768]), (12000, [8192, 12000])]
)
async def test_length_failure_is_bounded_and_not_cached(
    context, token_limit, budgets, no_retry_delay
):
    seen = []

    def handle(request):
        seen.append(json.loads(request.content)["max_completion_tokens"])
        return provider_response("openai", None, "length")

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(
                provider="openai", model="test", max_completion_tokens_limit=token_limit
            ),
            "http://provider.test/v1",
            "",
            client,
            cache,
        )
        with pytest.raises(DomainError, match=f"{token_limit}-token output/reasoning budget"):
            await engine.plan(context)
    assert seen == budgets
    assert not cache.entries


@pytest.mark.parametrize("status, expected_calls", [(400, 1), (401, 1), (429, 3), (500, 3)])
async def test_sdk_http_retries_are_owned_by_planner(
    context, status, expected_calls, no_retry_delay
):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"message": "sensitive provider detail"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider="openai", model="test"),
            "http://provider.test/v1",
            "secret",
            client,
            MemoryCache(),
        )
        with pytest.raises(DomainError, match=f"HTTP {status}") as error:
            await engine.plan(context)
    assert calls == expected_calls
    assert "sensitive provider detail" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("reason, refusal", [("content_filter", None), ("stop", "refused")])
async def test_refusals_are_not_retried_or_cached(context, reason, refusal):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return provider_response("openai", None, reason, refusal)

    cache = MemoryCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider="openai", model="test"),
            "http://provider.test/v1",
            "",
            client,
            cache,
        )
        with pytest.raises(DomainError, match="Provider (refused|filtered)"):
            await engine.plan(context)
    assert calls == 1 and not cache.entries


async def test_token_settings_invalidate_cache(context):
    plan = await GravityBaseline().plan(context)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return provider_response("openai", provider_json(plan))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider="openai", model="test"),
            "http://provider.test/v1",
            "",
            client,
            MemoryCache(),
        )
        await engine.plan(context)
        engine.config = engine.config.model_copy(update={"max_completion_tokens": 4096})
        await engine.plan(context)
        assert not client.is_closed
    assert len(calls) == 2
    assert calls[1]["max_completion_tokens"] == 4096


async def test_home_only_context_needs_no_llm(context):
    context = context.model_copy(
        update={
            "candidates": [c for c in context.candidates if c.id == context.agent.home_id],
        }
    )

    def handle(request):
        pytest.fail("A home-only context must not send an invalid empty enum schema")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        engine = StructuredProvider(
            ProviderConfig(provider="openai", model="test"),
            "http://provider.test/v1",
            "",
            client,
            MemoryCache(),
        )
        plan = await engine.plan(context)
    assert len(plan.activities) == 2
    assert all(a.destination_id == context.agent.home_id for a in plan.activities)


def test_token_budget_config_is_validated():
    with pytest.raises(ValueError, match="must not exceed"):
        ProviderConfig(provider="openai", model="test", max_completion_tokens_limit=4096)
