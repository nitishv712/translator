FROM python:3.11-slim

WORKDIR /app

# CPU-only torch first: ai4bharat-transliteration depends on torch without
# pinning a build, and pip's default resolution pulls the full CUDA wheel
# (several GB) for a service that only ever runs on CPU here. Installing the
# CPU build first means the later `pip install -r requirements.txt` finds
# torch already satisfied and leaves it alone.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8100

CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:8100", "--timeout", "120", "app:app"]
