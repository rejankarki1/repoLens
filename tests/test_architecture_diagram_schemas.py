"""`kind` normalization on the raw AI-facing schemas (`schemas/architecture_diagram.py`).

Added after a real Groq response used `kind: "component"` for a React component — a reasonable synonym for
our `"ui"` value that the strict `Literal` rejected outright, losing the whole (otherwise valid) response.
`kind` is a free-text classification, not a verification claim (see the module docstring), so normalizing a
near-miss here is safe in a way normalizing `confidence`/`evidence` would not be.
"""

import pytest

from apps.api.schemas.architecture_diagram import RawComponent, RawRelationship


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ui", "ui"),  # already canonical: passed through unchanged
        ("component", "ui"),  # the real Groq response that motivated this
        ("frontend", "ui"),
        ("controller", "api"),
        ("ROUTE", "api"),  # case-insensitive
        ("background job", "job"),  # spaces normalized like dashes/underscores
        ("totally-unrecognized-kind", "other"),  # never raises; unknowns fall back to "other"
    ],
)
def test_component_kind_normalizes_known_synonyms_and_falls_back_to_other(raw: str, expected: str) -> None:
    component = RawComponent.model_validate({"id": "c1", "label": "X", "kind": raw})
    assert component.kind == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("imports", "imports"),
        ("http_call", "sync_call"),
        ("api_call", "sync_call"),
        ("enqueues", "queue"),
        ("reads", "db_read"),
        ("writes", "db_write"),
        ("something_novel", "other"),
    ],
)
def test_relationship_kind_normalizes_known_synonyms_and_falls_back_to_other(raw: str, expected: str) -> None:
    relationship = RawRelationship.model_validate({"id": "r1", "source": "a", "target": "b", "label": "x", "kind": raw})
    assert relationship.kind == expected
