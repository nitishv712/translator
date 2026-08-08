"""
NLLB-200 (distilled 600M) translation, served via CTranslate2.

Swapped in for Argos Translate's own translation (which is no longer in
this stack at all) because Argos's fluency was noticeably worse for this
app's language pairs (e.g. "How is you" instead of "How are you"). NLLB is
Meta's open multilingual translation model; CTranslate2 is a runtime built
specifically for efficient CPU inference, unlike the research-oriented
fairseq/torch stack the transliterate service used to run — the lesson from
that (pin thread counts, don't trust framework defaults for how many CPUs
are actually available) is applied here too.

The model is baked into the image at build time (see Dockerfile), not
downloaded/converted at container start: conversion to CTranslate2's int8
format needs the full ~2.4GB fp32 checkpoint in memory, which is far more
than this stack budgets for a running container. Paying that cost once at
build time means the deployed container only ever loads the much smaller,
already-quantized weights.

Requests are served through a micro-batching queue rather than one at a
time. Measured against the previous arrangement — `inter_threads=1` with a
batch of one per request — the whole stack flattened out at ~3 req/s, and
past that latency just grew: at 8 concurrent callers the median response
had already doubled, and 100 in flight would have queued well past the
caller's own 30s timeout. Two things were wasted there. Only one core was
ever translating, despite the container being given two; and every request
paid full model-invocation overhead for a single short sentence, which is
the case batching exists for. A request now parks on a queue for a few
milliseconds while others accumulate, and the batch goes through in one
call. The window is short enough to be invisible next to translation
itself, and a batch of one behaves exactly as before.

The queue is bounded on purpose. Under a burst too big to serve, shedding
immediately with 503 keeps latency honest for the requests already in
flight, instead of accepting everything and timing all of them out.
"""

import os

# Must be set before ctranslate2/transformers do any work — left unpinned,
# these size their thread pools to the host's core count regardless of the
# container's `cpus:` quota, which is exactly what pegged the transliterate
# service's CPU at idle before that got fixed.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import queue
import threading
import time

import ctranslate2
from flask import Flask, jsonify, request
from transformers import AutoTokenizer

app = Flask(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/app/model")

# One translation per core. CTranslate2 splits a batch across `inter_threads`
# workers, so this is what actually decides how much of the container's
# `cpus:` quota gets used — it sat at 1 before, leaving half the budget idle.
# `intra_threads=1` keeps each worker on a single core rather than letting
# the two fight over both.
INTER_THREADS = int(os.environ.get("INTER_THREADS", "2"))
INTRA_THREADS = int(os.environ.get("INTRA_THREADS", "1"))

# How many requests can ride in one CTranslate2 call, and how long the first
# one waits for company. The window only has to cover the gap between
# near-simultaneous arrivals; anything longer is latency spent on a bet that
# more work is coming.
MAX_BATCH_SIZE = int(os.environ.get("MAX_BATCH_SIZE", "8"))
BATCH_WINDOW_SECONDS = float(os.environ.get("BATCH_WINDOW_MS", "15")) / 1000

# Depth at which new work is refused outright. Two things bound this: it has
# to absorb every caller of a realistic burst (~100 people tapping translate
# at once), and a full queue still has to drain inside JOB_TIMEOUT_SECONDS
# below, or the requests at the back were accepted only to expire. At the
# measured rate those meet comfortably — anything deeper would be promising
# people a translation that arrives after they've stopped waiting for it.
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "128"))

# Kept under both gunicorn's worker timeout and the orchestrator's own
# REQUEST_TIMEOUT, so a stuck batch surfaces here as a 504 rather than as a
# dropped connection upstream.
JOB_TIMEOUT_SECONDS = float(os.environ.get("JOB_TIMEOUT_SECONDS", "25"))

# FLORES-200 codes NLLB actually expects, keyed by the short codes this
# app's API (and LibreTranslate before it) uses everywhere else.
LANGUAGES = {
    "en": ("English", "eng_Latn"),
    "hi": ("Hindi", "hin_Deva"),
    "bn": ("Bengali", "ben_Beng"),
}

# Loaded once at process start, same reasoning as the transliterate
# service's model load: the container isn't "ready" until this finishes.
translator = ctranslate2.Translator(
    MODEL_DIR, device="cpu", inter_threads=INTER_THREADS, intra_threads=INTRA_THREADS
)
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)

# Setting `tokenizer.src_lang` mutates the tokenizer's underlying
# Rust-backed post-processor in place — under gunicorn's threaded worker,
# two requests hitting this concurrently raced and crashed with
# "RuntimeError: Already borrowed" (confirmed while load-testing this
# service). translate_batch() itself is fine to run concurrently — only
# the tokenizer mutate/encode/decode steps need to be serialized.
tokenizer_lock = threading.Lock()


class _Job:
    """One caller's request, waiting for the batch it gets folded into."""

    __slots__ = ("text", "source", "target", "done", "translated", "failed")

    def __init__(self, text, source, target):
        self.text = text
        self.source = source
        self.target = target
        self.done = threading.Event()
        self.translated = None
        self.failed = False


_pending = queue.Queue(maxsize=MAX_QUEUE_DEPTH)


def _collect_batch():
    """Blocks for the first job, then gathers whatever else shows up in the window."""
    jobs = [_pending.get()]
    deadline = time.monotonic() + BATCH_WINDOW_SECONDS
    while len(jobs) < MAX_BATCH_SIZE:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            jobs.append(_pending.get(timeout=remaining))
        except queue.Empty:
            break
    return jobs


def _run_batch(jobs):
    # Source language is per-item, and setting it mutates the tokenizer, so
    # the whole batch is encoded in one held stretch of the lock rather than
    # reacquiring it (and racing) per sentence.
    with tokenizer_lock:
        batch_tokens = []
        for job in jobs:
            tokenizer.src_lang = LANGUAGES[job.source][1]
            batch_tokens.append(tokenizer.convert_ids_to_tokens(tokenizer.encode(job.text)))

    # target_prefix is per-item too, so a single batch can serve callers
    # asking for different languages — they don't have to be grouped.
    results = translator.translate_batch(
        batch_tokens, target_prefix=[[LANGUAGES[job.target][1]] for job in jobs]
    )

    with tokenizer_lock:
        for job, result in zip(jobs, results):
            output_tokens = result.hypotheses[0][1:]  # drop the target-language prefix token
            job.translated = tokenizer.decode(tokenizer.convert_tokens_to_ids(output_tokens))


def _serve_batches():
    while True:
        jobs = _collect_batch()
        try:
            _run_batch(jobs)
        except Exception:  # noqa: BLE001 - one bad batch must not kill the loop
            app.logger.exception("Batch translation failed")
            for job in jobs:
                if job.translated is None:
                    job.failed = True
        finally:
            for job in jobs:
                job.done.set()


# Daemon so a shutdown isn't held up by a thread that never returns. Started
# at import: with gunicorn's single worker this runs exactly once, and the
# container is only ever "ready" once the model above has loaded anyway.
threading.Thread(target=_serve_batches, name="nllb-batcher", daemon=True).start()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/languages", methods=["GET"])
def languages():
    return jsonify([{"code": code, "name": name} for code, (name, _) in LANGUAGES.items()])


@app.route("/translate", methods=["POST"])
def translate():
    body = request.get_json(silent=True) or {}
    text = (body.get("q") or "").strip()
    source = body.get("source")
    target = body.get("target")

    if not text or source not in LANGUAGES or target not in LANGUAGES:
        return jsonify({"error": "q, a supported source, and a supported target are required"}), 400

    if source == target:
        return jsonify({"translatedText": text})

    job = _Job(text, source, target)
    try:
        _pending.put_nowait(job)
    except queue.Full:
        return jsonify({"error": "Translation service is busy"}), 503

    if not job.done.wait(timeout=JOB_TIMEOUT_SECONDS):
        return jsonify({"error": "Translation timed out"}), 504
    if job.failed:
        return jsonify({"error": "Translation failed"}), 500

    return jsonify({"translatedText": job.translated})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8300)
