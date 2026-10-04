# Egress detector companion app

A reference PII detector for Libra OS's outbound query guard. With `LIBRA_OS_EGRESS_GUARD=detector`, Libra OS sends every outbound web search query and every fetched URL here before it reaches a search provider. If the detector finds personal data, Libra OS **refuses** that search. It never removes the detected words and sends the rest.

Model: [GLiNER2-PII](https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi) (Apache-2.0), on CPU, with English, French and five other languages. The model is baked into the image at build time, and the container runs without network access to the model hub.

This is a reference. Any service that implements the contract below works. Detectors miss things, so treat this as one layer of defence and not as a guarantee.

## Contract

Libra OS sends:

```
POST /detect
Content-Type: application/json

{"text": "<the exact query or URL>", "kind": "search" | "fetch"}
```

The detector answers:

```json
{"pii": true, "spans": [{"label": "person", "start": 0, "end": 8}]}
```

- `pii: true` refuses the search. The refusal names the span labels, never the text.
- Any other answer also refuses the search, because the guard fails closed: a non-2xx status, a body that does not parse, a missing `pii` field, or no answer within `LIBRA_OS_EGRESS_DETECTOR_TIMEOUT` (default `2s`).
- `GET /healthz` returns `{"status": "ok"}`.

## Bring up

```bash
docker compose -f docker-compose.yml -f apps/egress-detector/docker-compose.yaml up -d --build
```

The first build downloads CPU PyTorch and the model, so it takes a few minutes. The service publishes no host port. Only containers on `nova-net` can reach it, because it receives the queries it checks.

## Wire to Libra OS

Add to the root `.env`:

```
LIBRA_OS_EGRESS_GUARD=detector
LIBRA_OS_EGRESS_DETECTOR_URL=http://egress-detector:8080/detect
```

Then run `docker compose up -d nova-os`. `GET /v1/managed/features` reports `egress_guard` as on.

You can also turn the guard on for one agent (frontmatter `egress_guard: detector`) or for one request (`metadata.egress_guard: "detector"`). Each level can add the guard but cannot remove one set by another level. In both cases `LIBRA_OS_EGRESS_DETECTOR_URL` must be set, or the search is refused.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `DETECTOR_MODEL` | `fastino/gliner2-privacy-filter-PII-multi` | Build arg and env. The model to bake and load. |
| `DETECTOR_LABELS` | names, contact details, IDs, account and card numbers | Comma-separated model labels to look for. Fewer labels is faster. |
| `DETECTOR_THRESHOLD` | `0.5` | Minimum confidence for a span. Lower finds more and refuses more. |

## Test

The contract tests need no model:

```bash
cd apps/egress-detector
pip install fastapi httpx pytest
pytest -q
```
