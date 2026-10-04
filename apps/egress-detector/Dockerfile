# Reference egress detector for Libra OS (LIBRA_OS_EGRESS_GUARD=detector).
# CPU only. GLiNER2-PII, Apache-2.0.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app

# CPU wheels for torch, so the image does not pull CUDA libraries.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.1,<3"
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the model into the image so the container starts without network
# access. Override DETECTOR_MODEL at build time to bake a different one.
ARG DETECTOR_MODEL=fastino/gliner2-privacy-filter-PII-multi
ENV DETECTOR_MODEL=${DETECTOR_MODEL}
RUN python -c "import os; from gliner2 import GLiNER2; GLiNER2.from_pretrained(os.environ['DETECTOR_MODEL'])"

COPY app.py .

RUN useradd --uid 10001 --no-create-home detector && chown -R detector /models
USER detector

# Offline at runtime: the model is already in /models.
ENV HF_HUB_OFFLINE=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
