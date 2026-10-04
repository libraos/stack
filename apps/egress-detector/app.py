"""Reference egress detector for Libra OS.

Libra OS can check every outbound web search query and fetched URL before it
reaches a search provider (LIBRA_OS_EGRESS_GUARD=detector). It POSTs

    {"text": "<query or URL>", "kind": "search" | "fetch"}

to this service and expects

    {"pii": bool, "spans": [{"label": str, "start": int, "end": int}]}

`pii: true` makes Libra OS refuse the search; it never strips the spans and
sends the rest. A non-2xx answer, a timeout or an unparseable body also
refuses (fail closed), so this service should answer quickly and only with
the shape above.

The model is GLiNER2-PII (Apache-2.0) on CPU by default. Any detector can be
swapped in by replacing `GlinerDetector`; the HTTP contract is what Libra OS
relies on.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Callable, Protocol

from fastapi import FastAPI
from pydantic import BaseModel, Field

DEFAULT_MODEL = "fastino/gliner2-privacy-filter-PII-multi"

# A subset of the model's PII labels that matters for a search query. The
# model conditions on the labels it is given, so fewer labels is faster.
DEFAULT_LABELS = (
    "person,full_name,first_name,last_name,date_of_birth,"
    "email,phone_number,address,street_address,postal_code,"
    "government_id,national_id_number,passport_number,drivers_license_number,tax_id,"
    "bank_account,account_number,iban,payment_card,card_number,"
    "username,ip_address,account_id"
)

MAX_TEXT_CHARS = 4000


class DetectRequest(BaseModel):
    text: str = Field(..., max_length=MAX_TEXT_CHARS)
    kind: str = "search"


class Span(BaseModel):
    label: str
    start: int
    end: int


class DetectResponse(BaseModel):
    pii: bool
    spans: list[Span]


class Detector(Protocol):
    def find(self, text: str) -> list[Span]: ...


class GlinerDetector:
    """GLiNER2-PII, loaded once and shared across requests."""

    def __init__(self, model_id: str, labels: list[str], threshold: float) -> None:
        from gliner2 import GLiNER2  # imported here so the tests need no model

        self._model = GLiNER2.from_pretrained(model_id)
        self._labels = labels
        self._threshold = threshold
        self._lock = threading.Lock()

    def find(self, text: str) -> list[Span]:
        with self._lock:
            result = self._model.extract_entities(
                text, self._labels, threshold=self._threshold, include_spans=True
            )
        return spans_from_result(text, result)


def gliner_from_env() -> Detector:
    labels = [l.strip() for l in os.getenv("DETECTOR_LABELS", DEFAULT_LABELS).split(",") if l.strip()]
    return GlinerDetector(
        os.getenv("DETECTOR_MODEL", DEFAULT_MODEL),
        labels,
        float(os.getenv("DETECTOR_THRESHOLD", "0.5")),
    )


def spans_from_result(text: str, result: Any) -> list[Span]:
    """Normalise the model output to spans.

    Entities may come back as plain strings or as dicts carrying start/end;
    a string is located in the text. A value that cannot be located is still
    reported (at 0..0): a detection is a detection even without an offset.
    """
    entities = result.get("entities", {}) if isinstance(result, dict) else {}
    spans: list[Span] = []
    for label, values in entities.items():
        for value in values or []:
            if isinstance(value, dict):
                start, end = value.get("start"), value.get("end")
                if isinstance(start, int) and isinstance(end, int):
                    spans.append(Span(label=label, start=start, end=end))
                    continue
                value = value.get("text", "")
            value = str(value)
            start = text.find(value) if value else -1
            if start >= 0:
                spans.append(Span(label=label, start=start, end=start + len(value)))
            else:
                spans.append(Span(label=label, start=0, end=0))
    spans.sort(key=lambda s: (s.start, s.end))
    return spans


def build_app(make_detector: Callable[[], Detector] = gliner_from_env) -> FastAPI:
    holder: dict[str, Detector] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Load at startup: the first query does not pay for it, and a model
        # that cannot load fails the container instead of every request.
        holder["d"] = make_detector()
        yield

    app = FastAPI(title="egress-detector", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/detect", response_model=DetectResponse)
    def detect(req: DetectRequest) -> DetectResponse:
        spans = holder["d"].find(req.text)
        return DetectResponse(pii=bool(spans), spans=spans)

    return app


app = build_app()
