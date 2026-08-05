from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ServiceType = Literal["airtime", "data", "electricity", "tv", "education", "gift_cards", "payments"]


@dataclass(slots=True)
class ProviderCandidate:
    """Represents a provider option for a service request."""

    name: str
    priority: int = 1
    available: bool = True
    status: str = "active"
    cost: float | None = None
    success_rate: float | None = None
    response_time_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderSelectionResult:
    """Container for the selected provider and ranking details."""

    selected_provider: str | None
    candidates: list[ProviderCandidate]
    ranked_candidates: list[ProviderCandidate]


class ProviderSelector:
    """Select the best provider candidate using weighted scoring."""

    def __init__(self) -> None:
        self._service_type: ServiceType | None = None

    def select_provider(
        self,
        candidates: list[ProviderCandidate],
        *,
        service_type: ServiceType | None = None,
    ) -> ProviderSelectionResult:
        """Rank candidates and return the best provider for the requested service."""
        active_candidates = [candidate for candidate in candidates if self._is_eligible(candidate)]
        ranked_candidates = sorted(active_candidates, key=self._score_candidate, reverse=True)

        selected_provider = ranked_candidates[0].name if ranked_candidates else None
        return ProviderSelectionResult(
            selected_provider=selected_provider,
            candidates=active_candidates,
            ranked_candidates=ranked_candidates,
        )

    def _is_eligible(self, candidate: ProviderCandidate) -> bool:
        """Return True when the candidate is allowed for selection."""
        if not candidate.available:
            return False
        if candidate.status.lower() not in {"active", "healthy", "online"}:
            return False
        return True

    def _score_candidate(self, candidate: ProviderCandidate) -> float:
        """Score a provider candidate using simple weighted heuristics."""
        priority_score = max(0, 100 - (candidate.priority * 10))
        availability_score = 100.0 if candidate.available else 0.0
        status_score = 100.0 if candidate.status.lower() in {"active", "healthy", "online"} else 0.0
        cost_score = 100.0 - (candidate.cost or 0.0) * 10.0
        success_score = (candidate.success_rate or 0.0) * 100.0
        response_score = max(0.0, 100.0 - (candidate.response_time_ms or 0) / 10.0)

        return (
            priority_score * 0.15
            + availability_score * 0.20
            + status_score * 0.20
            + cost_score * 0.15
            + success_score * 0.20
            + response_score * 0.10
        )


def select_provider(candidates: list[ProviderCandidate], *, service_type: ServiceType | None = None) -> ProviderSelectionResult:
    """Convenience wrapper for provider selection."""
    selector = ProviderSelector()
    return selector.select_provider(candidates, service_type=service_type)
