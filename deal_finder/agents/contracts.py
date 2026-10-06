"""Versioned contracts shared by deterministic agents and future AI adapters."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

PIPELINE_VERSION = 'agent-readiness-v0.8'
STATUSES = {'completed', 'blocked', 'needs_review', 'quarantined', 'failed'}

@dataclass(frozen=True)
class Result:
    agent: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    version: str = PIPELINE_VERSION

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError('Invalid agent status')

    def to_dict(self):
        return asdict(self)

@dataclass
class Context:
    raw: dict
    as_of: datetime
    candidates: list
    identity_evidence: dict = field(default_factory=dict)
    source_asking_candidates: list | None = None
    target: Any = None
    results: dict[str, Result] = field(default_factory=dict)

    def ready(self, *names):
        return all(n in self.results and self.results[n].status == 'completed' for n in names)


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timestamp requires timezone')
    return parsed.astimezone(timezone.utc)


def evidence(value):
    if not isinstance(value, str):
        raise ValueError('Evidence URL required')
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError('Evidence must have an HTTPS source URL')
    return value


def cents(value):
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError('Costs require nonnegative integer EUR cents')
    return value


def bounded_cost(row, as_of):
    """A human attestation is recorded; authenticity is not independently checked."""
    if not isinstance(row, dict) or row.get('verified') is not True:
        raise ValueError('Cost requires explicit human verification')
    if not isinstance(row.get('verified_by'), str) or not row['verified_by'].strip():
        raise ValueError('Cost requires verifier identity')
    evidence(row.get('evidence_url'))
    if instant(row['quoted_at']) > as_of or instant(row['valid_until']) < as_of:
        raise ValueError('Cost evidence unavailable or expired at analysis time')
    low, high = cents(row['low_cents']), cents(row['high_cents'])
    if low > high:
        raise ValueError('Cost lower bound exceeds upper bound')
    if row.get('currency') != 'EUR' or row.get('tax_included') is not True:
        raise ValueError('Costs must include tax and be denominated in EUR')
    return low, high


def verified_identity(proof, listing, as_of):
    try:
        if not isinstance(proof, dict) or proof.get('verified') is not True:
            return False
        if proof.get('vehicle_id') != listing.vehicle_id or not listing.vehicle_id:
            return False
        if not isinstance(proof.get('verified_by'), str) or not proof['verified_by'].strip():
            return False
        evidence(proof.get('evidence_url'))
        return instant(proof['verified_at']) <= as_of
    except (ValueError, KeyError, TypeError):
        return False
