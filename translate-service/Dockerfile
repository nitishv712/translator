FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8200

# Threaded, not two plain sync workers: this service does no real work of its
# own, it just waits on langid, transliterate and NLLB in turn. With `-w 2`
# alone exactly two requests could be in flight for the entire stack, so the
# orchestrator hit its ceiling long before the translation model hit one.
CMD ["gunicorn", "-w", "2", "--threads", "64", "-b", "0.0.0.0:8200", "--timeout", "60", "app:app"]
