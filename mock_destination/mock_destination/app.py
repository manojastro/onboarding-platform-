"""Mock destination API — the primary demo target (docs/DECISIONS.md, D1).

Behaves like a typical SaaS API, modelled on Stripe: bearer auth,
``Idempotency-Key`` on writes, token-bucket rate limiting with ``429`` +
``Retry-After``, and lookup by ``external_id`` for reconciliation.

It is deliberately *not* safe against careless clients: ``external_id`` is not
unique here, so a client that retries blindly creates duplicates, and
``GET /_admin/stats`` shows them.

Faults are injected through ``/_admin/faults`` so tests and demos can reproduce
failures deterministically. Faults other than ``timeout_after_commit`` fire
before the request executes: nothing is written and nothing is stored under the
idempotency key.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import secrets
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator, model_validator

# Mirrors the platform's currency allowlist (DECISIONS.md D5). A real
# destination enforces its own rules; this one happens to agree.
CURRENCY_MINOR_UNITS: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "JPY": 0, "CAD": 2, "AUD": 2}
IDEMPOTENCY_TTL_S = 24 * 3600
ENTITY_PREFIX = {"customers": "cus", "orders": "ord"}
SYSTEM_FIELDS = frozenset({"id", "object", "created_at", "updated_at", "version"})

FaultAction = Literal["error_500", "error_503", "rate_limit_429", "timeout_before_commit", "timeout_after_commit"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_AMOUNT_RE = re.compile(r"^\d+(?:\.(\d+))?$")


# --- Payload models -----------------------------------------------------------


def _iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not _ISO_DATE_RE.match(value):
        raise ValueError("must be an ISO date (YYYY-MM-DD)")
    date.fromisoformat(value)  # rejects impossible dates such as 2026-02-30
    return value


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CustomerFields(_Payload):
    email: StrictStr | None = Field(default=None, max_length=254)
    first_name: StrictStr | None = Field(default=None, max_length=200)
    last_name: StrictStr | None = Field(default=None, max_length=200)
    phone: StrictStr | None = Field(default=None, max_length=50)
    country_code: StrictStr | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    signup_date: StrictStr | None = None
    status: Literal["active", "inactive"] | None = None
    metadata: dict[str, StrictStr] | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is not None and not _EMAIL_RE.match(value):
            raise ValueError("invalid email address")
        return value

    @field_validator("signup_date")
    @classmethod
    def _signup_date(cls, value: str | None) -> str | None:
        return _iso_date(value)


class CustomerCreate(CustomerFields):
    external_id: StrictStr = Field(min_length=1, max_length=128)
    email: StrictStr = Field(max_length=254)


class CustomerUpdate(CustomerFields):
    pass


class OrderFields(_Payload):
    customer_id: StrictStr | None = None
    order_date: StrictStr | None = None
    total_amount: StrictStr | None = None
    currency: StrictStr | None = None
    status: Literal["pending", "paid", "shipped", "cancelled", "refunded"] | None = None
    metadata: dict[str, StrictStr] | None = None

    @field_validator("total_amount", mode="before")
    @classmethod
    def _amount_must_be_text(cls, value: Any) -> Any:
        if value is not None and not isinstance(value, str):
            raise ValueError(
                'must be a decimal string such as "19.99"; JSON numbers are rejected because they '
                "round-trip through binary floating point"
            )
        return value

    @field_validator("order_date")
    @classmethod
    def _order_date(cls, value: str | None) -> str | None:
        return _iso_date(value)

    @model_validator(mode="after")
    def _money(self) -> OrderFields:
        if self.currency is not None and self.currency not in CURRENCY_MINOR_UNITS:
            raise ValueError(f"unsupported currency {self.currency!r}")
        if self.total_amount is not None:
            match = _AMOUNT_RE.match(self.total_amount)
            if match is None:
                raise ValueError("total_amount must be a non-negative decimal string")
            if self.currency is not None:
                decimals = len(match.group(1) or "")
                allowed = CURRENCY_MINOR_UNITS[self.currency]
                if decimals > allowed:
                    raise ValueError(
                        f"total_amount has {decimals} decimal places but {self.currency} allows {allowed}"
                    )
        return self


class OrderCreate(OrderFields):
    external_id: StrictStr = Field(min_length=1, max_length=128)
    customer_id: StrictStr
    order_date: StrictStr
    total_amount: StrictStr
    currency: StrictStr


class OrderUpdate(OrderFields):
    pass


class FaultRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: FaultAction
    method: Literal["GET", "POST", "PATCH"] | None = None
    path_prefix: str | None = None
    after_requests: int = Field(default=0, ge=0, description="Matching requests to let through first")
    times: int = Field(default=1, ge=1, le=100_000, description="How many matching requests to affect")
    delay_s: float = Field(default=2.0, ge=0, le=60, description="Stall for the timeout_* actions")


class FaultRulesIn(BaseModel):
    rules: list[FaultRuleIn] = Field(min_length=1)


class RateLimitIn(BaseModel):
    requests_per_second: float = Field(gt=0, le=10_000)
    burst: int = Field(ge=1, le=10_000)


# --- State ---------------------------------------------------------------------


@dataclass
class FaultRule:
    id: str
    action: str
    method: str | None
    path_prefix: str | None
    after_requests: int
    times: int
    delay_s: float
    seen: int = 0
    fired: int = 0

    def matches(self, method: str, path: str) -> bool:
        return (self.method is None or self.method == method) and (
            self.path_prefix is None or path.startswith(self.path_prefix)
        )

    @property
    def exhausted(self) -> bool:
        return self.fired >= self.times


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def take(self) -> float | None:
        """Consume one token. Returns None when allowed, else seconds until a token is free."""
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return None
        return (1 - self.tokens) / self.rate


@dataclass
class _IdempotencyEntry:
    fingerprint: str
    created_at: float
    in_progress: bool = True
    status_code: int = 0
    body: Any = None


class MockState:
    def __init__(self, api_key: str, rate: float, burst: int) -> None:
        self.api_key = api_key
        self.default_rate_limit = (rate, burst)
        self.reset()

    def reset(self) -> None:
        self.records: dict[str, dict[str, dict[str, Any]]] = {"customers": {}, "orders": {}}
        self.idempotency: dict[str, _IdempotencyEntry] = {}
        self.faults: list[FaultRule] = []
        self.bucket = TokenBucket(*self.default_rate_limit)
        self.counters: Counter[str] = Counter()
        self.responses_by_status: Counter[int] = Counter()

    def match_fault(self, method: str, path: str) -> FaultRule | None:
        """Every matching live rule counts the request; the first eligible one fires."""
        fired: FaultRule | None = None
        for rule in self.faults:
            if rule.exhausted or not rule.matches(method, path):
                continue
            rule.seen += 1
            if fired is None and rule.seen > rule.after_requests:
                rule.fired += 1
                fired = rule
        if fired is not None:
            self.counters["faults_fired"] += 1
        return fired

    def prune_idempotency(self) -> None:
        cutoff = time.monotonic() - IDEMPOTENCY_TTL_S
        for key in [k for k, e in self.idempotency.items() if e.created_at < cutoff and not e.in_progress]:
            del self.idempotency[key]

    def snapshot(self) -> dict[str, Any]:
        duplicates: dict[str, dict[str, int]] = {}
        for kind, records in self.records.items():
            counts = Counter(r["external_id"] for r in records.values())
            duplicates[kind] = {ext: n for ext, n in sorted(counts.items()) if n > 1}
        return {
            "records": {kind: len(records) for kind, records in self.records.items()},
            "duplicate_external_ids": duplicates,
            "duplicate_count": sum(n - 1 for per_kind in duplicates.values() for n in per_kind.values()),
            "requests_total": self.counters["requests_total"],
            "responses_by_status": {str(k): v for k, v in sorted(self.responses_by_status.items())},
            "idempotent_replays": self.counters["idempotent_replays"],
            "rate_limited": self.counters["rate_limited"],
            "faults_fired": self.counters["faults_fired"],
            "writes": {
                "customers_created": self.counters["customers_created"],
                "customers_updated": self.counters["customers_updated"],
                "orders_created": self.counters["orders_created"],
                "orders_updated": self.counters["orders_updated"],
            },
            "rate_limit": {"requests_per_second": self.bucket.rate, "burst": int(self.bucket.capacity)},
        }


# --- Helpers -------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_body(type_: str, code: str, message: str, param: str | None = None) -> dict[str, Any]:
    return {"error": {"type": type_, "code": code, "message": message, "param": param}}


def _error(
    status: int, type_: str, code: str, message: str, *, param: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_body(type_, code, message, param), headers=headers)


def _describe_validation(errors: list[dict[str, Any]]) -> tuple[str, str | None]:
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or 'body'}: {err.get('msg')}")
    first_loc = [str(p) for p in (errors[0].get("loc", ()) if errors else ()) if p != "body"]
    return "; ".join(parts), (".".join(first_loc) or None)


def _fingerprint(method: str, path: str, raw_body: bytes) -> str:
    try:
        canonical = json.dumps(json.loads(raw_body or b"null"), sort_keys=True, separators=(",", ":"))
    except ValueError:
        canonical = raw_body.decode("utf-8", "replace")
    return hashlib.sha256(f"{method} {path}\n{canonical}".encode()).hexdigest()


# --- App -----------------------------------------------------------------------


def create_app(
    *, api_key: str | None = None, rate_limit_rps: float | None = None, rate_limit_burst: int | None = None
) -> FastAPI:
    state = MockState(
        api_key=api_key or os.environ.get("MOCK_API_KEY", "mock_sk_test_local"),
        rate=rate_limit_rps if rate_limit_rps is not None else float(os.environ.get("MOCK_RATE_LIMIT_RPS", "50")),
        burst=rate_limit_burst if rate_limit_burst is not None else int(os.environ.get("MOCK_RATE_LIMIT_BURST", "50")),
    )
    app = FastAPI(title="Mock Destination API", version="1.0.0")
    app.state.mock = state

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        message, param = _describe_validation(list(exc.errors()))
        return _error(400, "invalid_request_error", "parameter_invalid", message, param=param)

    # -- Gatekeeper: auth, faults, rate limit ----------------------------------

    @app.middleware("http")
    async def gatekeeper(request: Request, call_next: Callable[..., Any]) -> Any:
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        response = await _gate(request, call_next)
        state.responses_by_status[response.status_code] += 1
        return response

    async def _gate(request: Request, call_next: Callable[..., Any]) -> Any:
        state.counters["requests_total"] += 1
        if request.headers.get("authorization") != f"Bearer {state.api_key}":
            return _error(401, "authentication_error", "unauthorized", "Missing or invalid API key.")

        rule = state.match_fault(request.method, request.url.path)
        if rule is not None and rule.action == "rate_limit_429":
            state.counters["rate_limited"] += 1
            return _error(429, "rate_limit_error", "rate_limited", "Too many requests (injected).",
                          headers={"Retry-After": "1"})
        wait = state.bucket.take()
        if wait is not None:
            state.counters["rate_limited"] += 1
            return _error(429, "rate_limit_error", "rate_limited", "Too many requests.",
                          headers={"Retry-After": str(max(1, math.ceil(wait)))})
        if rule is not None:
            if rule.action == "error_500":
                return _error(500, "api_error", "internal_error", "Injected internal error. Nothing was written.")
            if rule.action == "error_503":
                return _error(503, "api_error", "service_unavailable", "Injected outage. Nothing was written.",
                              headers={"Retry-After": "1"})
            if rule.action == "timeout_before_commit":
                await asyncio.sleep(rule.delay_s)
                return _error(504, "api_error", "gateway_timeout",
                              "Injected timeout before commit. Nothing was written.")

        response = await call_next(request)
        if rule is not None and rule.action == "timeout_after_commit":
            # The write is committed and its idempotent result stored; only the reply is late.
            await asyncio.sleep(rule.delay_s)
        return response

    # -- Idempotency -------------------------------------------------------------

    async def _idempotent(
        request: Request, key: str | None, operation: Callable[[], tuple[int, dict[str, Any]]]
    ) -> JSONResponse:
        if key is None:
            status, body = operation()
            return JSONResponse(status_code=status, content=body)
        raw = await request.body()  # read before the check-and-set so no await splits it
        fingerprint = _fingerprint(request.method, request.url.path, raw)
        state.prune_idempotency()
        entry = state.idempotency.get(key)
        if entry is not None:
            if entry.fingerprint != fingerprint:
                return _error(400, "idempotency_error", "idempotency_key_reused",
                              "Keys for idempotent requests can only be used with the same parameters "
                              "they were first used with.")
            if entry.in_progress:
                return _error(409, "idempotency_error", "idempotency_request_in_progress",
                              "A request with this idempotency key is still being processed.")
            state.counters["idempotent_replays"] += 1
            return JSONResponse(status_code=entry.status_code, content=copy.deepcopy(entry.body),
                                headers={"Idempotent-Replayed": "true", "Idempotency-Key": key})
        state.idempotency[key] = _IdempotencyEntry(fingerprint=fingerprint, created_at=time.monotonic())
        try:
            status, body = operation()
        except BaseException:
            state.idempotency.pop(key, None)
            raise
        entry = state.idempotency[key]
        entry.in_progress, entry.status_code, entry.body = False, status, copy.deepcopy(body)
        return JSONResponse(status_code=status, content=body, headers={"Idempotency-Key": key})

    # -- Record operations -------------------------------------------------------

    def _missing_customer(customer_id: str) -> tuple[int, dict[str, Any]] | None:
        if customer_id not in state.records["customers"]:
            return 400, _error_body("invalid_request_error", "resource_missing",
                                    f"No such customer: {customer_id!r}", param="customer_id")
        return None

    def _create(kind: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if kind == "orders" and (missing := _missing_customer(data["customer_id"])):
            return missing
        now = _now()
        record = {
            "id": f"{ENTITY_PREFIX[kind]}_{secrets.token_hex(8)}",
            "object": kind[:-1],
            **data,
            "metadata": data.get("metadata") or {},
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
        state.records[kind][record["id"]] = record
        state.counters[f"{kind}_created"] += 1
        return 201, copy.deepcopy(record)

    def _update(kind: str, record_id: str, changes: dict[str, Any], full_model: type[BaseModel]) -> tuple[int, dict]:
        current = state.records[kind].get(record_id)
        if current is None:
            return 404, _error_body("invalid_request_error", "resource_missing",
                                    f"No such {kind[:-1]}: {record_id!r}", param="id")
        merged = {k: v for k, v in {**current, **changes}.items() if k not in SYSTEM_FIELDS}
        try:
            full_model.model_validate(merged)
        except ValidationError as exc:
            message, param = _describe_validation(list(exc.errors()))
            return 400, _error_body("invalid_request_error", "parameter_invalid", message, param)
        if kind == "orders" and (missing := _missing_customer(merged["customer_id"])):
            return missing
        current.update(changes)
        current["updated_at"] = _now()
        current["version"] += 1
        state.counters[f"{kind}_updated"] += 1
        return 200, copy.deepcopy(current)

    def _list(kind: str, external_id: str | None, limit: int) -> dict[str, Any]:
        items = [r for r in state.records[kind].values() if external_id is None or r["external_id"] == external_id]
        return {"object": "list", "data": copy.deepcopy(items[:limit]), "has_more": len(items) > limit}

    def _get(kind: str, record_id: str) -> JSONResponse:
        record = state.records[kind].get(record_id)
        if record is None:
            return _error(404, "invalid_request_error", "resource_missing", f"No such {kind[:-1]}: {record_id!r}",
                          param="id")
        return JSONResponse(copy.deepcopy(record))

    # -- Public API ----------------------------------------------------------------

    @app.post("/v1/customers", status_code=201)
    async def create_customer(payload: CustomerCreate, request: Request,
                              idempotency_key: str | None = Header(default=None)) -> JSONResponse:
        return await _idempotent(request, idempotency_key, lambda: _create("customers", payload.model_dump()))

    @app.patch("/v1/customers/{record_id}")
    async def update_customer(record_id: str, payload: CustomerUpdate, request: Request,
                              idempotency_key: str | None = Header(default=None)) -> JSONResponse:
        changes = payload.model_dump(exclude_unset=True)
        return await _idempotent(request, idempotency_key,
                                 lambda: _update("customers", record_id, changes, CustomerCreate))

    @app.get("/v1/customers")
    async def list_customers(external_id: str | None = None,
                             limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        return _list("customers", external_id, limit)

    @app.get("/v1/customers/{record_id}")
    async def get_customer(record_id: str) -> JSONResponse:
        return _get("customers", record_id)

    @app.post("/v1/orders", status_code=201)
    async def create_order(payload: OrderCreate, request: Request,
                           idempotency_key: str | None = Header(default=None)) -> JSONResponse:
        return await _idempotent(request, idempotency_key, lambda: _create("orders", payload.model_dump()))

    @app.patch("/v1/orders/{record_id}")
    async def update_order(record_id: str, payload: OrderUpdate, request: Request,
                           idempotency_key: str | None = Header(default=None)) -> JSONResponse:
        changes = payload.model_dump(exclude_unset=True)
        return await _idempotent(request, idempotency_key,
                                 lambda: _update("orders", record_id, changes, OrderCreate))

    @app.get("/v1/orders")
    async def list_orders(external_id: str | None = None,
                          limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        return _list("orders", external_id, limit)

    @app.get("/v1/orders/{record_id}")
    async def get_order(record_id: str) -> JSONResponse:
        return _get("orders", record_id)

    # -- Admin (unauthenticated: this is a local test double) ----------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/_admin/reset")
    async def reset() -> dict[str, bool]:
        state.reset()
        return {"ok": True}

    @app.get("/_admin/stats")
    async def stats() -> dict[str, Any]:
        return state.snapshot()

    @app.get("/_admin/records/{kind}")
    async def records(kind: Literal["customers", "orders"]) -> dict[str, Any]:
        return {"data": copy.deepcopy(list(state.records[kind].values()))}

    @app.get("/_admin/faults")
    async def list_faults() -> dict[str, Any]:
        return {"rules": [asdict(r) for r in state.faults]}

    @app.post("/_admin/faults", status_code=201)
    async def add_faults(body: FaultRulesIn) -> dict[str, Any]:
        created = [FaultRule(id=f"fault_{secrets.token_hex(4)}", **rule.model_dump()) for rule in body.rules]
        state.faults.extend(created)
        return {"rules": [asdict(r) for r in created]}

    @app.delete("/_admin/faults")
    async def clear_faults() -> dict[str, bool]:
        state.faults.clear()
        return {"ok": True}

    @app.put("/_admin/rate-limit")
    async def set_rate_limit(body: RateLimitIn) -> dict[str, Any]:
        state.bucket = TokenBucket(body.requests_per_second, body.burst)
        return {"requests_per_second": body.requests_per_second, "burst": body.burst}

    return app


app = create_app()
