"""Provider registry + degradation ladder (ADR-004 §2, Team-A §6/§15).

$0-only **by construction**:
- a non-local provider cannot be *enabled* — `ProviderSpec.__post_init__` and
  `ProviderRegistry.register` both reject it, so an enabled cloud entry is
  unconstructible (not merely discouraged);
- `attempt()` raises `ProviderUnavailable` for every provider in this build —
  no clients exist; when a guard-gated local client lands (Ollama first), only
  this function's body changes;
- no HTTP appears in this module — the CI no-bypass scan (tests/test_guard.py)
  covers every non-guard source file, including this one.

Ladder semantics (route()): enabled + capability match + context floor +
available (fail-closed: unprobed = unavailable), ordered local-first, then by
priority, then name. An empty route means "park the task" — never a wider
search, never a paid fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

# auth types — the only three the $0 policy admits as *declarable*
AUTH_LOCAL = "local"                      # Ollama / LM Studio / local models
AUTH_SUBSCRIPTION_WEB = "subscription_web"  # declared only; no client; guard-denied
AUTH_API_KEY_FREE = "api_key_free"        # reserved; no client; guard-denied

AUTH_TYPES = (AUTH_LOCAL, AUTH_SUBSCRIPTION_WEB, AUTH_API_KEY_FREE)


class ProviderUnavailable(RuntimeError):
    """Raised when a route attempt has no callable client (always, in this build)
    or when the ladder is exhausted. The runner parks the run on this exception."""


@dataclass(frozen=True)
class ProviderSpec:
    """One declared provider. `enabled=True` is only constructible for AUTH_LOCAL —
    the structural $0 invariant."""

    name: str
    auth_type: str
    capabilities: frozenset[str]
    context_limit: int
    priority: int = 50          # lower = preferred within its tier
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.auth_type not in AUTH_TYPES:
            raise ValueError(f"unknown auth_type: {self.auth_type}")
        if self.auth_type != AUTH_LOCAL and self.enabled:
            raise ValueError(
                f"$0 violation: non-local provider '{self.name}' cannot be enabled")

    @property
    def is_local(self) -> bool:
        return self.auth_type == AUTH_LOCAL


class ProviderRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}

    def register(self, spec: ProviderSpec) -> None:
        if spec.auth_type != AUTH_LOCAL and spec.enabled:  # defense in depth
            raise ValueError(
                f"$0 violation: cannot register enabled non-local provider '{spec.name}'")
        if spec.name in self._specs:
            raise ValueError(f"duplicate provider: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ProviderSpec:
        if name not in self._specs:
            raise KeyError(f"unknown provider: {name}")
        return self._specs[name]

    def all(self) -> list[ProviderSpec]:
        return list(self._specs.values())


class AvailabilityCache:
    """Fail-closed availability: a provider is unavailable unless probed ok.
    No I/O here — probes are injected callables run by the scheduler, never by
    the router (Team-A §6: consult the cache, never probe synchronously)."""

    def __init__(self) -> None:
        self._ok: dict[str, tuple[bool, str]] = {}

    def mark(self, name: str, ok: bool, reason: str = "") -> None:
        self._ok[name] = (ok, reason)

    def ok(self, name: str) -> bool:
        return self._ok.get(name, (False, "never probed"))[0]


def default_registry() -> ProviderRegistry:
    """The declared world. Cloud entries exist so the ladder's *ordering and
    filtering* are real and testable — they are constructible only disabled."""
    reg = ProviderRegistry()
    reg.register(ProviderSpec(
        name="ollama-local", auth_type=AUTH_LOCAL,
        capabilities=frozenset({"plan", "classify", "summarize", "extract"}),
        context_limit=8192, priority=10, enabled=True,
        description="Local models via Ollama — the floor of the ladder; "
                    "default planner brain Qwen2.5-3B-Instruct "
                    "(V3 decision record, matrix-gated; best-available after "
                    "the locate-family amendment)"))
    reg.register(ProviderSpec(
        name="whisper-local", auth_type=AUTH_LOCAL,
        capabilities=frozenset({"transcribe"}),
        context_limit=0, priority=10, enabled=True,
        description="Local transcription for the content pipeline (V2)"))
    for name, caps, ctx, prio in [
        ("chatgpt-subscription", ("plan", "research"), 128_000, 20),
        ("claude-subscription", ("code", "plan"), 200_000, 20),
        ("gemini-subscription", ("research", "multimodal"), 1_000_000, 30),
    ]:
        reg.register(ProviderSpec(
            name=name, auth_type=AUTH_SUBSCRIPTION_WEB,
            capabilities=frozenset(caps), context_limit=ctx,
            priority=prio, enabled=False,
            description="declared only: no client, guard-denied egress"))
    reg.register(ProviderSpec(
        name="free-tier-reserved", auth_type=AUTH_API_KEY_FREE,
        capabilities=frozenset({"classify"}), context_limit=8_000,
        priority=40, enabled=False,
        description="reserved: no client; guard denies egress regardless"))
    return reg


def route(registry: ProviderRegistry, task_class: str, min_context: int = 0,
          availability: AvailabilityCache | None = None) -> list[ProviderSpec]:
    """The degradation ladder: local-first, then priority, then name."""
    candidates = [
        s for s in registry.all()
        if s.enabled
        and task_class in s.capabilities
        and s.context_limit >= min_context
        and (availability is None or availability.ok(s.name))
    ]
    candidates.sort(key=lambda s: (0 if s.is_local else 1, s.priority, s.name))
    return candidates


def attempt(spec: ProviderSpec) -> str:
    """Execute one model call. V1 skeleton: no clients exist, so every attempt
    raises — the ladder's fall-through is real and testable. The model-backed
    client (local Ollama first, guard-gated) replaces ONLY this body."""
    raise ProviderUnavailable(
        f"provider '{spec.name}' has no client in this build ($0 skeleton)")
