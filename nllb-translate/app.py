"""
NLLB-200 (distilled 600M) translation, served via CTranslate2.

Swapped in for Argos Translate's own translation (LibreTranslate is still
in this stack, but only for its /detect endpoint now — see
translate-service/app.py) because Argos's fluency was noticeably worse for
this app's language pairs (e.g. "How is you" instead of "How are you").
NLLB is Meta's open multilingual translation model; CTranslate2 is a
runtime built specifically for efficient CPU inference, unlike the
research-oriented fairseq/torch stack the transliterate service used to
run — the lesson from that (pin thread counts, don't trust framework
defaults for how many CPUs are actually available) is applied here too.

The model is baked into the image at build time (see Dockerfile), not
downloaded/converted at container start: conversion to CTranslate2's int8
format needs the full ~2.4GB fp32 checkpoint in memory, which is far more
than this stack budgets for a running container. Paying that cost once at
build time means the deployed container only ever loads the much smaller,
already-quantized weights.
"""

import os

# Must be set before ctranslate2/transformers do any work — left unpinned,
# these size their thread pools to the host's core count regardless of the
# container's `cpus:` quota, which is exactly what pegged the transliterate
# service's CPU at idle before that got fixed.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import threading

import ctranslate2
from flask import Flask, jsonify, request
from transformers import AutoTokenizer

app = Flask(__name__)

MODEL_DIR = os.environ.get("MODEL_DIR", "/app/model")

# FLORES-200 codes NLLB actually expects, keyed by the short codes this
# app's API (and LibreTranslate before it) uses everywhere else.
LANGUAGES = {
    "en": ("English", "eng_Latn"),
    "hi": ("Hindi", "hin_Deva"),
    "bn": ("Bengali", "ben_Beng"),
}

# Loaded once at process start, same reasoning as the transliterate
# service's model load: the container isn't "ready" until this finishes.
translator = ctranslate2.Translator(MODEL_DIR, device="cpu", inter_threads=1, intra_threads=1)
tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)

# Setting `tokenizer.src_lang` mutates the tokenizer's underlying
# Rust-backed post-processor in place — under gunicorn's threaded worker,
# two requests hitting this concurrently raced and crashed with
# "RuntimeError: Already borrowed" (confirmed while load-testing this
# service). translate_batch() itself is fine to run concurrently — only
# the tokenizer mutate/encode/decode steps need to be serialized.
tokenizer_lock = threading.Lock()


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

    with tokenizer_lock:
        tokenizer.src_lang = LANGUAGES[source][1]
        source_tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(text))

    target_prefix = [LANGUAGES[target][1]]
    results = translator.translate_batch([source_tokens], target_prefix=[target_prefix])
    output_tokens = results[0].hypotheses[0][1:]  # drop the target-language prefix token

    with tokenizer_lock:
        translated = tokenizer.decode(tokenizer.convert_tokens_to_ids(output_tokens))

    return jsonify({"translatedText": translated})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8300)
