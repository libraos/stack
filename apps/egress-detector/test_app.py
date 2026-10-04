"""Contract tests for the egress detector. No model is loaded."""

from fastapi.testclient import TestClient

from app import Span, build_app, spans_from_result


class FakeDetector:
    def find(self, text: str) -> list[Span]:
        i = text.find("Jane Doe")
        return [Span(label="person", start=i, end=i + 8)] if i >= 0 else []


def client() -> TestClient:
    return TestClient(build_app(lambda: FakeDetector()))


def test_hit_reports_pii_and_spans():
    with client() as c:
        r = c.post("/detect", json={"text": "Jane Doe wrongful dismissal", "kind": "search"})
    assert r.status_code == 200
    assert r.json() == {"pii": True, "spans": [{"label": "person", "start": 0, "end": 8}]}


def test_miss_reports_no_pii():
    with client() as c:
        r = c.post("/detect", json={"text": "wrongful dismissal notice period", "kind": "search"})
    assert r.json() == {"pii": False, "spans": []}


def test_fetch_kind_is_accepted():
    with client() as c:
        r = c.post("/detect", json={"text": "https://example.org/?q=Jane+Doe", "kind": "fetch"})
    assert r.status_code == 200


def test_missing_text_is_rejected():
    # A non-2xx answer makes Libra OS refuse the search (fail closed).
    with client() as c:
        r = c.post("/detect", json={"kind": "search"})
    assert r.status_code == 422


def test_spans_from_result_accepts_strings_and_dicts():
    text = "Call Jane Doe at jane@example.org"
    got = spans_from_result(
        text,
        {"entities": {"person": ["Jane Doe"], "email": [{"text": "jane@example.org", "start": 17, "end": 33}]}},
    )
    assert [(s.label, s.start, s.end) for s in got] == [("person", 5, 13), ("email", 17, 33)]
    # An unlocatable value is still a detection.
    assert spans_from_result("x", {"entities": {"person": ["zzz"]}})[0].label == "person"
    assert spans_from_result("x", None) == []
