# Stage 1: fetch the GlotLID checkpoint. Separate stage so curl and its CA
# bundle stay out of the final image — only the model file is carried over.
FROM python:3.11-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned to a commit rather than `main`: this is a 1.7GB classifier whose
# behaviour the detection thresholds in app.py were tuned against, so a
# silently updated model would move those numbers under us. Baked in at build
# time, like the NLLB service's weights, so a container restart never waits on
# (or fails because of) a download.
RUN curl -fsSL -o /model.bin \
    https://huggingface.co/cis-lmu/glotlid/resolve/85cd6716494360367b75f642b5bc78667605d0b4/model_v3.bin

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /model.bin ./model/glotlid.bin
COPY app.py .

EXPOSE 8400

# One worker, threaded: each gunicorn worker loads its own ~1.7GB copy of the
# model, so a second one would double this container's memory for no gain —
# prediction is CPU-cheap and the threads absorb concurrent requests fine.
CMD ["gunicorn", "-w", "1", "--threads", "16", "-b", "0.0.0.0:8400", "--timeout", "60", "app:app"]
