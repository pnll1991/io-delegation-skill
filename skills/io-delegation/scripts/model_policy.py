"""Host-aware model policy for Jev-guided worker orchestration.

Policy is local and user-editable. Jev produces task suitability/risk scores; this
module maps those scores to an approved model profile. It never invents model IDs or
changes provider/adapter outside the reviewed worker configuration.
"""
from __future__ import annotations

import math
from typing import Any

PRESETS = ("cost", "balanced", "quality")
HOSTS = ("codex", "cursor", "claude-code")
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

# Defaults are policy weights, not benchmark percentages. Cursor ordering is informed
# by CursorBench 4.0 (2026-09-10); Codex ordering follows current OpenAI model
# positioning/pricing. User configuration can replace every profile/order/ceiling.
DEFAULT_PROFILES = {
    "codex": [
        dict(id="luna-medium", model="gpt-5.6-luna", effort="medium",
             capability=.30, cost_index=.020, source="openai-pricing-2026-09"),
        dict(id="luna-high", model="gpt-5.6-luna", effort="high",
             capability=.45, cost_index=.024, source="openai-pricing-plus-live-2026-09"),
        dict(id="terra-medium", model="gpt-5.6-terra", effort="medium",
             capability=.58, cost_index=.20, source="openai-pricing-2026-09"),
        dict(id="sol-medium", model="gpt-5.6-sol", effort="medium",
             capability=.76, cost_index=.40, source="openai-pricing-2026-09"),
        # Product hard ceiling requested by default: Astra is never above low.
        dict(id="astra-low", model="gpt-6-astra", effort="low",
             capability=1.00, cost_index=1.00, source="openai-pricing-2026-09"),
    ],
    "cursor": [
        # CursorBench 4.0: Luna medium $0.08 / 22.2%, Luna high $0.25 / 29.4%.
        dict(id="luna-medium", model="gpt-5.6-luna", effort="medium",
             cursor_model="gpt-5.6-luna", capability=.40, cost_index=.03,
             benchmark_score=.222, benchmark_cost=.08, benchmark_steps=32,
             source="cursorbench-4.0-2026-09-10"),
        dict(id="luna-high", model="gpt-5.6-luna", effort="high",
             cursor_model="gpt-5.6-luna[effort=high]", capability=.58, cost_index=.09,
             benchmark_score=.294, benchmark_cost=.25, benchmark_steps=64,
             source="cursorbench-4.0-2026-09-10"),
        # Retained for user overrides/latency preference; balanced default can skip it.
        dict(id="terra-medium", model="gpt-5.6-terra", effort="medium",
             cursor_model="gpt-5.6-terra", capability=.56, cost_index=.23,
             benchmark_score=.276, benchmark_cost=.64, benchmark_steps=25,
             source="cursorbench-4.0-2026-09-10"),
        dict(id="sol-medium", model="gpt-5.6-sol", effort="medium",
             cursor_model="gpt-5.6-sol", capability=.72, cost_index=.63,
             benchmark_score=.311, benchmark_cost=1.77, benchmark_steps=32,
             source="cursorbench-4.0-2026-09-10"),
        dict(id="sol-high", model="gpt-5.6-sol", effort="high",
             cursor_model="gpt-5.6-sol[effort=high]", capability=.90, cost_index=1.00,
             benchmark_score=.357, benchmark_cost=2.85, benchmark_steps=41,
             source="cursorbench-4.0-2026-09-10"),
    ],
}

DEFAULT_ORDERS = {
    "codex": {
        "cost": ["luna-medium", "luna-high", "terra-medium", "sol-medium", "astra-low"],
        "balanced": ["luna-medium", "luna-high", "terra-medium", "sol-medium", "astra-low"],
        "quality": ["luna-high", "terra-medium", "sol-medium", "astra-low"],
    },
    "cursor": {
        # Terra medium is dominated by Luna high on CursorBench score/cost, so cost and
        # balanced skip it by default. Users can restore it when latency matters.
        "cost": ["luna-medium", "luna-high", "sol-medium"],
        "balanced": ["luna-medium", "luna-high", "sol-medium", "sol-high"],
        "quality": ["luna-high", "sol-medium", "sol-high"],
    },
}

DEFAULT_MAX = {"codex": "astra-low", "cursor": "sol-high"}
PRESET_BIAS = {"cost": -.10, "balanced": 0.0, "quality": .10}


class ModelPolicyError(ValueError):
    pass


def _finite(value: Any, name: str, lo: float = 0.0, hi: float = 1.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ModelPolicyError(f"{name} must be between {lo} and {hi}")
    return float(value)


def _positive(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ModelPolicyError(f"{name} must be a non-negative number")
    return float(value)


def _profile(item: Any, host: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ModelPolicyError("model profile must be an object")
    allowed = {
        "id", "model", "effort", "cursor_model", "capability", "cost_index",
        "max_output_tokens", "benchmark_score", "benchmark_cost", "benchmark_steps",
        "source",
    }
    if set(item) - allowed:
        raise ModelPolicyError("unknown model profile option")
    pid = item.get("id")
    model = item.get("model")
    if not isinstance(pid, str) or not pid.strip():
        raise ModelPolicyError("model profile id required")
    if not isinstance(model, str) or not model.strip():
        raise ModelPolicyError("model profile requires model")
    effort = item.get("effort", "medium")
    if effort not in EFFORTS:
        raise ModelPolicyError("invalid model reasoning effort")
    result = dict(item)
    result["id"] = pid.strip()
    result["model"] = model.strip()
    result["effort"] = effort
    result["capability"] = _finite(item.get("capability", .5), "profile capability")
    result["cost_index"] = _positive(item.get("cost_index", .5), "profile cost_index")
    if "max_output_tokens" in item:
        value = item["max_output_tokens"]
        if type(value) is not int or not 64 <= value <= 32768:
            raise ModelPolicyError("profile max_output_tokens must be 64..32768")
    if "cursor_model" in item and (not isinstance(item["cursor_model"], str) or not item["cursor_model"].strip()):
        raise ModelPolicyError("cursor_model must be a non-empty string")
    for key in ("benchmark_score", "benchmark_cost", "benchmark_steps"):
        if key in item:
            result[key] = _positive(item[key], key)
    return result


def _host_policy(raw: Any, host: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ModelPolicyError(f"{host} model policy must be an object")
    allowed = {
        "profiles", "orders", "max_profile", "min_profile", "blocked_profiles",
        "allowed_profiles", "max_escalations", "allow_escalation", "strategy",
        "native_router_model",
    }
    if set(raw) - allowed:
        raise ModelPolicyError(f"unknown {host} model policy option")
    defaults = [dict(x) for x in DEFAULT_PROFILES.get(host, ())]
    profiles = raw.get("profiles", defaults)
    if not isinstance(profiles, list) or not profiles:
        raise ModelPolicyError(f"{host} profiles must be a non-empty list")
    parsed = [_profile(x, host) for x in profiles]
    ids = [x["id"] for x in parsed]
    if len(ids) != len(set(ids)):
        raise ModelPolicyError(f"duplicate {host} model profile id")
    table = {x["id"]: x for x in parsed}

    orders = raw.get("orders", DEFAULT_ORDERS.get(host, {}))
    if not isinstance(orders, dict) or set(orders) - set(PRESETS):
        raise ModelPolicyError(f"{host} orders must contain only cost/balanced/quality")
    normalized_orders = {}
    for preset in PRESETS:
        order = orders.get(preset) or ids
        if not isinstance(order, list) or not order or any(x not in table for x in order):
            raise ModelPolicyError(f"invalid {host} {preset} profile order")
        if len(order) != len(set(order)):
            raise ModelPolicyError(f"duplicate profile in {host} {preset} order")
        normalized_orders[preset] = list(order)

    default_max = (normalized_orders["balanced"][-1]
                   if "profiles" in raw else DEFAULT_MAX.get(host, normalized_orders["balanced"][-1]))
    max_profile = raw.get("max_profile", default_max)
    min_profile = raw.get("min_profile")
    if max_profile not in table:
        raise ModelPolicyError(f"unknown {host} max_profile")
    if min_profile is not None and min_profile not in table:
        raise ModelPolicyError(f"unknown {host} min_profile")
    blocked = raw.get("blocked_profiles", [])
    allowed_profiles = raw.get("allowed_profiles")
    if not isinstance(blocked, list) or any(x not in table for x in blocked):
        raise ModelPolicyError(f"invalid {host} blocked_profiles")
    if allowed_profiles is not None and (
        not isinstance(allowed_profiles, list) or not allowed_profiles or any(x not in table for x in allowed_profiles)
    ):
        raise ModelPolicyError(f"invalid {host} allowed_profiles")
    max_escalations = raw.get("max_escalations", 2)
    if type(max_escalations) is not int or not 0 <= max_escalations <= 8:
        raise ModelPolicyError("max_escalations must be 0..8")
    allow_escalation = raw.get("allow_escalation", True)
    if type(allow_escalation) is not bool:
        raise ModelPolicyError("allow_escalation must be boolean")
    strategy = raw.get("strategy", "direct")
    if strategy not in ("direct", "native-router-first"):
        raise ModelPolicyError("strategy must be direct or native-router-first")
    native = raw.get("native_router_model")
    if native is not None and (not isinstance(native, str) or not native.strip()):
        raise ModelPolicyError("native_router_model must be a non-empty string")
    if strategy == "native-router-first" and host != "cursor":
        raise ModelPolicyError("native-router-first is supported only for Cursor")
    if strategy == "native-router-first" and not native:
        raise ModelPolicyError("native-router-first requires native_router_model")
    return {
        "profiles": parsed, "table": table, "orders": normalized_orders,
        "max_profile": max_profile, "min_profile": min_profile,
        "blocked_profiles": list(blocked),
        "allowed_profiles": list(allowed_profiles) if allowed_profiles is not None else None,
        "max_escalations": max_escalations, "allow_escalation": allow_escalation,
        "strategy": strategy, "native_router_model": native,
    }


def resolve(cfg: dict[str, Any], host: str | None, preset_override: str | None = None) -> dict[str, Any]:
    policy = cfg.get("model_policy", {})
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ModelPolicyError("model_policy must be an object")
    allowed = {"preset", "hosts"}
    if set(policy) - allowed:
        raise ModelPolicyError("unknown model_policy option")
    preset = preset_override or policy.get("preset", "balanced")
    if preset not in PRESETS:
        raise ModelPolicyError("model_policy preset must be cost, balanced or quality")
    hosts = policy.get("hosts", {})
    if not isinstance(hosts, dict) or any(x not in HOSTS for x in hosts):
        raise ModelPolicyError("model_policy.hosts contains an unsupported host")
    effective_host = host
    if effective_host not in ("codex", "cursor"):
        adapter = cfg.get("adapter")
        effective_host = "codex" if adapter == "codex-cli" else "cursor" if adapter == "cursor-cli" else None
    if effective_host not in ("codex", "cursor"):
        return {
            "host": effective_host, "preset": preset, "profiles": [], "table": {}, "order": [],
            "max_profile": None, "min_profile": None, "max_escalations": 0,
            "allow_escalation": False, "strategy": "direct", "native_router_model": None,
        }
    hp = _host_policy(hosts.get(effective_host), effective_host)
    order = list(hp["orders"][preset])
    allowed_profiles = hp["allowed_profiles"]
    order = [x for x in order if x not in hp["blocked_profiles"]
             and (allowed_profiles is None or x in allowed_profiles)]
    registry_order = [item["id"] for item in hp["profiles"]]
    min_rank = registry_order.index(hp["min_profile"]) if hp["min_profile"] is not None else 0
    max_rank = registry_order.index(hp["max_profile"])
    order = [
        pid for pid in order
        if min_rank <= registry_order.index(pid) <= max_rank
    ]
    if not order:
        raise ModelPolicyError(f"{effective_host} model policy has no runnable profiles")
    return {
        **hp, "host": effective_host, "preset": preset, "order": order,
    }


def validate_config(cfg: dict[str, Any]) -> None:
    policy = cfg.get("model_policy")
    if policy is None:
        return
    # Validate every configured/default host so a latent bad branch cannot activate later.
    hosts = policy.get("hosts", {}) if isinstance(policy, dict) else {}
    targets = set(hosts) or {"codex", "cursor"}
    for host in targets:
        resolve(cfg, host)


def task_demand(scores: dict[str, Any], preset: str = "balanced") -> float:
    if preset not in PRESETS:
        raise ModelPolicyError("unknown model policy preset")
    cheap = _finite(scores.get("cheap_model_sufficient", .5), "cheap_model_sufficient")
    reasoning = _finite(scores.get("reasoning_required", .5), "reasoning_required")
    risk = _finite(scores.get("risk_high", .5), "risk_high")
    uncertainty = _finite(scores.get("uncertainty_high", .5), "uncertainty_high")
    parallel = _finite(scores.get("parallelism_useful", 0), "parallelism_useful")
    # Missing-corpus uncertainty is deliberately a weak model-strength signal:
    # Jev sees metadata, not fragment bodies, so buying a stronger model cannot recover
    # unseen evidence. The local evidence validator/unknowns path handles that case.
    # Cheap-model sufficiency, reasoning and risk drive capability demand.
    base = max(
        1.0 - cheap,
        reasoning,
        .70 * risk + .30 * reasoning,
        .20 * uncertainty + .10 * parallel,
    )
    return min(1.0, max(0.0, base + PRESET_BIAS[preset]))


def choose(cfg: dict[str, Any], host: str | None, scores: dict[str, Any], operation: str,
           preset_override: str | None = None) -> dict[str, Any]:
    policy = resolve(cfg, host, preset_override)
    if operation in {"debugging", "architecture", "security", "editing", "generation"}:
        return dict(decision="principal", reason="sensitive_operation", host=policy["host"],
                    preset=policy["preset"], demand=1.0, profile=None, model_tier=None)
    if not policy["order"]:
        return dict(decision="principal", reason="host_worker_unavailable", host=policy["host"],
                    preset=policy["preset"], demand=1.0, profile=None, model_tier=None)

    # Cursor Router can be explicitly selected by the user/team. We do not guess its
    # CLI identifier or optimization mode; native_router_model is an exact reviewed string.
    if policy["strategy"] == "native-router-first":
        profile = {
            "id": "cursor-native-router", "model": policy["native_router_model"],
            "cursor_model": policy["native_router_model"], "effort": "medium",
            "capability": 1.0, "cost_index": 0.0, "source": "cursor-native-router",
        }
        return dict(decision="worker", reason="cursor_native_router", host="cursor",
                    preset=policy["preset"], demand=task_demand(scores, policy["preset"]),
                    profile=profile, model_tier="M-auto", order=["cursor-native-router"],
                    max_escalations=0, allow_escalation=False)

    demand = task_demand(scores, policy["preset"])
    table = policy["table"]
    eligible = [
        (index, table[pid])
        for index, pid in enumerate(policy["order"])
        if table[pid]["capability"] >= demand
    ]
    selected = None
    selected_index = None
    if eligible:
        # Capability is already a hard eligibility filter. Do not divide cost by
        # capability again or stronger models get double credit and creep upward.
        # Presets trade absolute normalized cost against quality margin and latency.
        def objective(row):
            index, profile = row
            capability = max(.01, float(profile["capability"]))
            expected_cost = float(profile["cost_index"])
            quality_penalty = 1.0 - capability
            steps = float(profile.get("benchmark_steps", 0) or 0)
            latency_penalty = min(1.0, steps / 100.0) if steps else 0.0
            if policy["preset"] == "cost":
                value = .92 * expected_cost + .08 * latency_penalty
            elif policy["preset"] == "quality":
                value = .25 * expected_cost + .70 * quality_penalty + .05 * latency_penalty
            else:
                value = .75 * expected_cost + .20 * quality_penalty + .05 * latency_penalty
            return (value, index)
        selected_index, selected = min(eligible, key=objective)
    if selected is None:
        return dict(decision="principal", reason="model_ceiling_insufficient",
                    host=policy["host"], preset=policy["preset"], demand=demand,
                    profile=None, model_tier=None, order=policy["order"],
                    max_profile=policy["max_profile"])
    return dict(decision="worker", reason="minimum_sufficient_profile",
                host=policy["host"], preset=policy["preset"], demand=demand,
                profile=dict(selected), profile_index=selected_index,
                model_tier=f"M{selected_index+1}", order=policy["order"],
                max_profile=policy["max_profile"],
                max_escalations=policy["max_escalations"],
                allow_escalation=policy["allow_escalation"])


def next_profile(cfg: dict[str, Any], host: str | None, current: str,
                 preset_override: str | None = None) -> dict[str, Any] | None:
    policy = resolve(cfg, host, preset_override)
    if not policy["allow_escalation"] or current not in policy["order"]:
        return None
    index = policy["order"].index(current) + 1
    if index >= len(policy["order"]):
        return None
    item = dict(policy["table"][policy["order"][index]])
    return {"profile": item, "model_tier": f"M{index+1}", "profile_index": index}


def _cursor_model(profile: dict[str, Any]) -> str:
    explicit = profile.get("cursor_model")
    if explicit:
        return explicit
    model = profile["model"]
    effort = profile.get("effort", "medium")
    # Medium is the documented default for GPT-5.6. Avoid unnecessary variant syntax
    # unless the policy explicitly needs a different effort.
    return model if effort == "medium" else f"{model}[effort={effort}]"


def apply_profile(cfg: dict[str, Any], host: str | None, profile: dict[str, Any],
                  max_output_tokens: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    derived = dict(cfg)
    adapter = cfg.get("adapter")
    if adapter == "host-cli":
        if host == "codex":
            adapter = "codex-cli"
            if cfg.get("codex_executable"):
                derived["executable"] = cfg["codex_executable"]
        elif host == "cursor":
            adapter = "cursor-cli"
            if cfg.get("cursor_executable"):
                derived["executable"] = cfg["cursor_executable"]
        else:
            raise ModelPolicyError("host-cli requires Codex or Cursor host")
        derived["adapter"] = adapter
    if adapter == "command":
        raise ModelPolicyError("opaque command workers cannot switch model profiles")
    derived["model"] = profile["model"]
    derived["reasoning_effort"] = profile.get("effort", "medium")
    if adapter == "cursor-cli":
        derived["cursor_model"] = _cursor_model(profile)
    if "max_output_tokens" in profile:
        derived["reader_max_tokens"] = profile["max_output_tokens"]
    if adapter == "chat-completions" and max_output_tokens is not None:
        derived["reader_max_tokens"] = min(
            int(derived.get("reader_max_tokens", 1600)), int(max_output_tokens)
        )
    return derived, {
        "profile": profile["id"],
        "model": derived.get("model"),
        "reasoning_effort": derived.get("reasoning_effort"),
        "adapter": adapter,
        "cursor_model": derived.get("cursor_model"),
        "output_token_cap_supported": adapter == "chat-completions",
        "capability": profile.get("capability"),
        "cost_index": profile.get("cost_index"),
        "source": profile.get("source"),
    }


def summary(cfg: dict[str, Any], hosts: list[str] | tuple[str, ...],
            preset_override: str | None = None) -> dict[str, Any]:
    result = {}
    adapter = cfg.get("adapter")
    explicit = cfg.get("model_policy") is not None
    if not explicit and adapter != "host-cli":
        return result
    for host in hosts:
        if host not in ("codex", "cursor"):
            continue
        if not explicit and adapter == "codex-cli" and host != "codex":
            continue
        if not explicit and adapter == "cursor-cli" and host != "cursor":
            continue
        try:
            policy = resolve(cfg, host, preset_override)
            result[host] = {
                "preset": policy["preset"], "strategy": policy["strategy"],
                "order": policy["order"], "max_profile": policy["max_profile"],
                "max_escalations": policy["max_escalations"],
            }
        except ModelPolicyError as exc:
            result[host] = {"error": str(exc)}
    return result
