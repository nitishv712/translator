# Stage 1: download the official NLLB-200 checkpoint and convert it to
# CTranslate2's int8 format. Kept as a separate build stage so none of the
# ~2.4GB fp32 checkpoint or the conversion tooling ends up in the final
# image — only the much smaller quantized output does.
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir ctranslate2 transformers sentencepiece huggingface_hub

# transformers needs a backend framework to actually load the checkpoint
# before ct2-transformers-converter can convert it — CPU-only build since
# this container never touches a GPU; confirmed by hand that the converter
# fails with "NameError: name 'torch' is not defined" without this.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Full fp32 checkpoint, held in memory only for this one-time conversion —
# see app.py's docstring for why this happens at build time, not runtime.
RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('facebook/nllb-200-distilled-600M', local_dir='/build/hf-model')"

RUN ct2-transformers-converter \
    --model /build/hf-model \
    --output_dir /build/model \
    --quantization int8 \
    --force

# Tokenizer files live alongside the converted weights so app.py can load
# both from the same MODEL_DIR.
RUN python -c "from transformers import AutoTokenizer; \
    AutoTokenizer.from_pretrained('/build/hf-model').save_pretrained('/build/model')"

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /build/model ./model
COPY app.py .

EXPOSE 8300

# Still one worker — a second would load its own copy of the model, and the
# batcher thread inside this one is what actually feeds CTranslate2. The
# threads only need to be numerous enough to hold the queue's worth of
# callers while they wait on it; they spend that time blocked, not running.
CMD ["gunicorn", "-w", "1", "--threads", "128", "-b", "0.0.0.0:8300", "--timeout", "60", "app:app"]
