"""The Gemini API key pool: rotation, failover on RESOURCE_EXHAUSTED, waiting and throttling."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from invoice_auditor.config import Settings
from invoice_auditor.ingestion.gemini import (
    REST_DAILY_QUOTA,
    REST_RATE_LIMIT,
    REST_REJECTED,
    SDK_RETRY_STATUS_CODES,
    ExtractionError,
    ExtractionProgress,
    ExtractionThrottled,
    GeminiInvoiceExtractor,
    seconds_until_daily_reset,
)
from invoice_auditor.ingestion.key_pool import KeyPool

PDF = b"%PDF-1.7"
OK = SimpleNamespace(text='{"vendor_name": "ABC LLC", "total_amount": 10, "confidence": 94}', candidates=[])


def rate_limited(retry_delay: str | None = "37s", quota_id: str = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier") -> genai_errors.APIError:
    details: list[dict] = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
    ]
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    body = {"error": {"code": 429, "message": "You exceeded your current quota.", "status": "RESOURCE_EXHAUSTED", "details": details}}
    return genai_errors.ClientError(429, body)


def invalid_key() -> genai_errors.APIError:
    body = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}],
        }
    }
    return genai_errors.ClientError(400, body)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeKey:
    """One key's client: answers from a script, then with OK."""

    def __init__(self, name: str, log: list[str], script: list | None = None) -> None:
        self.name = name
        self.log = log
        self.script = list(script or [])
        self.aio = SimpleNamespace(models=self)

    async def generate_content(self, *, model, contents, config):
        self.log.append(self.name)
        outcome = self.script.pop(0) if self.script else OK
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def pooled(*scripts: list, clock: Clock | None = None, max_wait: float = 30.0) -> tuple[GeminiInvoiceExtractor, list[str], Clock]:
    clock = clock or Clock()
    extractor = GeminiInvoiceExtractor(
        api_keys=[f"secret-{index}" for index in range(len(scripts))],
        model="gemini-test",
        max_throttle_wait_s=max_wait,
        clock=clock,
        sleep=clock.sleep,
    )
    log: list[str] = []
    for index, (key, script) in enumerate(zip(extractor.pool.keys, scripts, strict=True), start=1):
        key.client = FakeKey(f"key {index}", log, script)
    extractor.pool._cursor = 0  # deterministic rotation for the assertions
    return extractor, log, clock


class TestRotation:
    async def test_requests_take_keys_in_turn(self):
        extractor, log, _ = pooled([], [], [])
        for _ in range(4):
            await extractor.extract(PDF, "application/pdf")
        assert log == ["key 1", "key 2", "key 3", "key 1"]
        assert [key.requests for key in extractor.pool.keys] == [2, 1, 1]

    def test_duplicate_keys_count_once_and_keys_are_not_exposed(self):
        extractor = GeminiInvoiceExtractor(api_keys=["AIza-first-secret", "AIza-second-secret", "AIza-first-secret"], api_key="AIza-second-secret", model="m")
        assert extractor.key_count == 2
        assert [key.label for key in extractor.pool.keys] == ["key 1", "key 2"]
        assert "secret" not in repr(extractor.pool.keys)

    def test_a_key_is_required(self):
        with pytest.raises(ValueError, match="At least one"):
            GeminiInvoiceExtractor(api_keys=[], model="m")

    def test_the_sdk_leaves_429_to_the_pool(self):
        assert 429 not in SDK_RETRY_STATUS_CODES

    def test_cold_pools_start_on_different_keys(self):
        starts = {KeyPool(["a", "b", "c", "d", "e"]).acquire().label for _ in range(60)}
        assert len(starts) > 1


class TestFailover:
    async def test_rate_limited_key_hands_over_to_the_next_one(self):
        extractor, log, clock = pooled([rate_limited("37s")], [], [])
        progress = ExtractionProgress()
        extraction = await extractor.extract(PDF, "application/pdf", progress=progress)

        assert extraction.vendor_name == "ABC LLC"
        assert log == ["key 1", "key 2"]
        assert progress.key_switches == 1 and progress.changed.is_set()
        first = extractor.pool.keys[0]
        assert first.reason == REST_RATE_LIMIT
        assert first.available_at == pytest.approx(clock.now + 38)  # Google's hint plus a second
        assert clock.sleeps == []

        # The resting key is skipped until its delay has passed.
        await extractor.extract(PDF, "application/pdf")
        await extractor.extract(PDF, "application/pdf")
        assert log[2:] == ["key 3", "key 2"]
        clock.now += 39
        await extractor.extract(PDF, "application/pdf")
        assert log[-1] == "key 3" and extractor.pool.available() == 3

    async def test_without_a_hint_the_key_rests_for_the_configured_cooldown(self):
        extractor, _, clock = pooled([rate_limited(retry_delay=None)], [])
        await extractor.extract(PDF, "application/pdf")
        assert extractor.pool.keys[0].available_at == pytest.approx(clock.now + 60)

    async def test_daily_quota_rests_the_key_until_the_reset(self):
        extractor, log, clock = pooled([rate_limited("20s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier")], [])
        await extractor.extract(PDF, "application/pdf")
        first = extractor.pool.keys[0]
        assert log == ["key 1", "key 2"]
        assert first.reason == REST_DAILY_QUOTA
        assert first.available_at - clock.now > 60

    async def test_an_invalid_key_is_set_aside(self):
        extractor, log, _ = pooled([invalid_key()], [])
        await extractor.extract(PDF, "application/pdf")
        assert log == ["key 1", "key 2"]
        assert extractor.pool.keys[0].reason == REST_REJECTED

    async def test_every_key_rejected_is_an_error_not_throttling(self):
        extractor, _, _ = pooled([invalid_key()], [invalid_key()])
        with pytest.raises(ExtractionError, match="rejected every configured API key") as caught:
            await extractor.extract(PDF, "application/pdf")
        assert not isinstance(caught.value, ExtractionThrottled)
        assert "secret-" not in str(caught.value)

    async def test_other_errors_fail_without_trying_more_keys(self):
        not_found = genai_errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
        extractor, log, _ = pooled([not_found], [])
        with pytest.raises(ExtractionError, match="404 NOT_FOUND"):
            await extractor.extract(PDF, "application/pdf")
        assert log == ["key 1"]


class TestThrottling:
    async def test_waits_for_the_first_key_back_when_every_key_is_resting(self):
        extractor, log, clock = pooled([rate_limited("5s")], [rate_limited("9s")])
        progress = ExtractionProgress()
        await extractor.extract(PDF, "application/pdf", progress=progress)

        assert log == ["key 1", "key 2", "key 1"]
        assert clock.sleeps == [pytest.approx(6)]
        assert progress.key_switches == 2 and progress.waiting_until is None

    async def test_reports_the_wait_while_it_waits(self):
        clock = Clock()
        seen: list[dict] = []
        extractor, _, _ = pooled([rate_limited("5s")], clock=clock)
        progress = ExtractionProgress()

        async def sleep(seconds: float) -> None:
            seen.append(progress.snapshot(clock.now))
            await clock.sleep(seconds)

        extractor._sleep = sleep
        await extractor.extract(PDF, "application/pdf", progress=progress)
        assert seen == [{"key_switches": 1, "waiting_ms": 6000}]

    async def test_every_key_exhausted_for_longer_than_the_wait_is_throttled(self):
        extractor, log, clock = pooled([rate_limited("50s")], [rate_limited("45s")], max_wait=30)
        with pytest.raises(ExtractionThrottled) as caught:
            await extractor.extract(PDF, "application/pdf")

        error = caught.value
        assert log == ["key 1", "key 2"] and clock.sleeps == []
        assert error.retry_after_s == pytest.approx(46) and error.keys == 2 and error.daily is False
        assert str(error) == "All 2 Gemini API keys are at their rate limits right now, so this batch is throttled. Try again in 46 seconds."

    async def test_wait_budget_covers_the_whole_upload(self):
        # Two 20 s waits would exceed a 30 s budget: the second is refused.
        extractor, _, clock = pooled([rate_limited("19s"), rate_limited("19s")], max_wait=30)
        with pytest.raises(ExtractionThrottled):
            await extractor.extract(PDF, "application/pdf")
        assert clock.sleeps == [pytest.approx(20)]

    async def test_daily_quota_on_every_key_says_so(self):
        daily = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        extractor, _, _ = pooled([rate_limited(None, quota_id=daily)])
        with pytest.raises(ExtractionThrottled, match="The Gemini API key is out of today's free quota") as caught:
            await extractor.extract(PDF, "application/pdf")
        assert caught.value.daily is True and caught.value.retry_after_s > 60


def test_daily_quota_resets_at_midnight_pacific():
    assert seconds_until_daily_reset(datetime(2026, 1, 10, 7, 30, tzinfo=UTC)) == pytest.approx(31 * 60)  # 23:30 PST
    assert seconds_until_daily_reset(datetime(2026, 9, 17, 6, 30, tzinfo=UTC)) == pytest.approx(31 * 60)  # 23:30 PDT
    assert seconds_until_daily_reset(datetime(2026, 9, 17, 7, 30, tzinfo=UTC)) == pytest.approx(24 * 3600 - 29 * 60)


class TestSettings:
    def test_keys_from_a_list_the_single_variable_and_numbered_variables(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY_2", " key-two ")
        monkeypatch.setenv("GEMINI_API_KEY_10", "key-ten")
        monkeypatch.setenv("GEMINI_API_KEY_3", "   ")
        settings = Settings(_env_file=None, gemini_api_keys="key-a, key-b;\nkey-c key-a", gemini_api_key="key-b")
        assert settings.gemini_key_pool == ["key-a", "key-b", "key-c", "key-two", "key-ten"]

    def test_numbered_keys_are_read_from_the_env_file_and_the_environment_wins(self, monkeypatch, tmp_path):
        for name in [name for name in __import__("os").environ if name.upper().startswith("GEMINI_API_KEY")]:
            monkeypatch.delenv(name)
        env_file = tmp_path / ".env"
        env_file.write_text("GEMINI_API_KEY_1=file-one\nGEMINI_API_KEY_2=file-two\n# GEMINI_API_KEY_3=commented\n", encoding="utf-8")
        monkeypatch.setenv("GEMINI_API_KEY_2", "env-two")
        settings = Settings(_env_file=env_file)
        assert settings.gemini_key_pool == ["file-one", "env-two"]
        assert "gemini_numbered_api_keys" not in settings.model_dump()

    def test_no_keys(self, monkeypatch):
        for name in [name for name in __import__("os").environ if name.upper().startswith("GEMINI_API_KEY")]:
            monkeypatch.delenv(name)
        assert Settings(_env_file=None).gemini_key_pool == []
