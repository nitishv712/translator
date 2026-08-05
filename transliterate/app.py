"""
Romanized Hindi ("Hinglish") -> Devanagari transliteration.

Argos Translate's Hindi model (like Google ML Kit's before it) was trained
on native Devanagari text — fed "toh kaise hai aap" directly, it doesn't
reliably recognize that as Hindi at all, since that's out-of-distribution
input for it. This service is the missing step: convert romanized text to
proper Devanagari first, so the translation model downstream gets input it
actually understands.

Uses AI4Bharat's IndicXlit (github.com/AI4Bharat/IndicXlit), a transformer
model trained specifically on real-world romanized/colloquial Indic text
(the Aksharantar corpus) rather than a formal transliteration scheme —
the closest fit for how people actually type Hinglish.
"""

import torch
from flask import Flask, jsonify, request

# Since PyTorch 2.6, torch.load() defaults to weights_only=True (a security
# hardening — untrusted checkpoints can't smuggle in arbitrary pickled
# objects). fairseq's own checkpoint loader (used internally by
# ai4bharat-transliteration to load the IndicXlit model) calls torch.load()
# without opting out, and the checkpoint stores its config as a pickled
# argparse.Namespace, which isn't on the safe-globals allowlist — so it
# fails to load on any torch >=2.6. We can't fix fairseq's call site (it's
# third-party code), and pinning an older torch to dodge this isn't
# reliable — a transitive dependency's own requirement can silently force a
# newer version back in regardless of what's pre-installed. Patching the
# default here works no matter which torch version actually gets resolved.
# Safe in this case: the checkpoint is IndicXlit's own official release, a
# known, trusted source — not arbitrary user-supplied input.
_original_torch_load = torch.load


def _torch_load_full(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _original_torch_load(*args, **kwargs)


torch.load = _torch_load_full

from ai4bharat.transliteration import XlitEngine  # noqa: E402 - must follow the patch above

app = Flask(__name__)

# Loaded once at process start (not per-request) — this also means the
# container isn't "ready" until this finishes, same as LibreTranslate
# loading its own models on boot.
engine = XlitEngine("hi", beam_width=4, rescore=True)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/transliterate", methods=["POST"])
def transliterate():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    try:
        result = engine.translit_sentence(text)
        transliterated = result.get("hi", text)
        return jsonify({"transliterated": transliterated})
    except Exception as e:  # noqa: BLE001 - surface any model error to the caller
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8100)
