# ai4bharat-transliteration pulls in fairseq, which doesn't build cleanly on
# Python >=3.10 — 3.9 is the newest version it's actually known to work on.
FROM python:3.9-slim

WORKDIR /app

# fairseq compiles C++ extensions from source (e.g. ngram_repeat_block) —
# the slim image has no compiler at all, so that build fails immediately
# without one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first: ai4bharat-transliteration depends on torch without
# pinning a build, and pip's default resolution pulls the full CUDA wheel
# (several GB) for a service that only ever runs on CPU here. Installing the
# CPU build first means the later `pip install -r requirements.txt` finds
# torch already satisfied and leaves it alone.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# fairseq's C++ extensions are written against numpy's older C API and fail
# to build against numpy 2.x, which pip would otherwise resolve to. Pinning
# this (and Cython, which fairseq also needs at build time) before it's
# pulled in transitively is what actually gets it to compile.
RUN pip install --no-cache-dir "numpy<1.24" "Cython<3"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8100

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8100", "--timeout", "120", "app:app"]
