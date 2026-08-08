# Translator

A standalone translation API. Fully self-hosted, no external API keys or
per-request cost — English, Hindi, and Bengali, with automatic language
detection and a transliteration step for romanized Hindi ("Hinglish", e.g.
`"toh kaise hai aap"`) that a translation model can't read on its own.

Deployed and run independently of any app that uses it (see
[Deploying](#deploying)) — it doesn't know or care who's calling it.

## Architecture

```
                       host machine, port 6010
                                 │
                 ┌───────────────▼───────────────┐
                 │  translate  (Flask, :8200)    │ ← the only published port
                 │  orchestrator                 │
                 └───┬──────────┬─────────────┬──┘
                     │          │             │
           ┌─────────▼┐  ┌──────▼───────┐  ┌──▼─────────────┐
           │  langid  │  │ transliterate│  │ nllb-translate │
           │ (GlotLID,│  │ (romanized → │  │ (NLLB-200 600M,│
           │ fastText)│  │  Devanagari) │  │  CTranslate2)  │
           │  :8400   │  │    :8100     │  │     :8300      │
           └──────────┘  └──────────────┘  └────────────────┘
```

All three are internal-only — nothing outside this compose stack can reach
them directly, only `translate` can.

`translate` runs each request through the same three steps:

1. **Identify** — `langid` returns one of the three supported languages and
   whether the text is romanized Indic. Native script is settled by Unicode
   range; only Latin-script text reaches the model, since "real English or
   romanized Hindi?" is the one genuinely ambiguous question here.
2. **Transliterate** — romanized text goes through `transliterate` first, so
   the translation model gets Devanagari instead of Latin text it reads as
   out-of-distribution.
3. **Translate** — `nllb-translate` does the real work, folding requests that
   arrive close together into a single model call.

Detection used to be LibreTranslate's `/detect`, which is why that service
stayed in the stack after Argos Translate stopped doing the translating. It
reported Hinglish as *confident* English often enough that step 2 got
skipped, the source came back as English, and a request asking for English
then found nothing to translate and echoed the caller's own text back at
them. GlotLID replaced it because it has explicit labels for romanized
Indic text; `langid/app.py` covers how its output is read and why the
threshold sits where it does.

## API

Base URL: `http://<host>:6010` (or `http://localhost:6010` for local dev).

### `POST /translate`

**Request body**

| field    | type   | required | notes                                                              |
|----------|--------|----------|---------------------------------------------------------------------|
| `text`   | string | yes      | The text to translate.                                              |
| `target` | string | yes      | `"en"`, `"hi"`, or `"bn"`.                                          |
| `source` | string | no       | `"en"`, `"hi"`, `"bn"`, `"hinglish"`, or omitted/`"auto"` (default). |

Leaving `source` out (or `"auto"`) is the normal case — detection and the
Hinglish fallback both only run when you don't already know the source.

**Response — `200 OK`**

```json
{ "translatedText": "How are you" }
```

**Response — error** (`400` bad request, `502` upstream translation error,
`503` a downstream service is unreachable)

```json
{ "error": "text and target are required" }
```

### `GET /languages`

Proxies the translation model's supported-language list straight through.

### `GET /health`

`{ "status": "ok" }` — liveness check, no auth, no dependencies checked.

## Using it from Node.js

This service does **no authentication of its own** — it's meant to sit
behind something that already knows who the caller is (see
[Auth](#auth--who-should-call-this)). From Node, that just means a plain
`fetch`/HTTP POST:

```js
// Node 18+ has fetch built in — no extra dependency needed.
async function translate(text, target, source = "auto") {
  const response = await fetch("http://localhost:6010/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, target, source }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Translation failed");
  }
  return data.translatedText;
}

const result = await translate("toh kaise hai aap", "en");
console.log(result); // "How are you"
```

With `axios`, if that's already a dependency in your project:

```js
const axios = require("axios");

async function translate(text, target, source = "auto") {
  const { data } = await axios.post("http://localhost:6010/translate", {
    text,
    target,
    source,
  });
  return data.translatedText;
}
```

### Real example: how talkmos-backend calls it

talkmos-backend doesn't call this service directly from wherever a
translation is needed — it has one thin proxy route,
[`src/modules/translate/translateController.js`](../talkmos-backend/src/modules/translate/translateController.js),
that:

1. Authenticates the request (`protect` middleware — a valid JWT) before it
   ever reaches this service, since this service has no auth of its own.
2. Forwards the client's `{ text, target, source }` body straight through.
3. Relays this service's response (or its error) back to the client.

```js
const TRANSLATE_SERVICE_URL =
  process.env.TRANSLATE_SERVICE_URL || "http://host.docker.internal:6010";

const translateText = async (req, res) => {
  try {
    const response = await fetch(`${TRANSLATE_SERVICE_URL}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    const data = await response.json();

    if (!response.ok) {
      const status = response.status === 400 ? 400 : 502;
      return res.status(status).json({ success: false, message: data.error });
    }
    return res.json({ success: true, data: { translatedText: data.translatedText } });
  } catch (error) {
    return res.status(503).json({ success: false, message: "Translation service is unavailable" });
  }
};
```

`host.docker.internal` is how talkmos-backend's own container reaches this
stack's published port from inside Docker on Linux — see its
`docker-compose.yml` `extra_hosts` entry. If you're calling this from a
plain Node process (not inside a container), `http://localhost:6010` is
enough.

### Auth — who should call this

Nothing here checks who's asking. Anything that can reach port 6010 can
translate for free, so **don't publish this port to the public internet**
directly — put it behind something that authenticates first (like
talkmos-backend's proxy route above), or firewall it to only the hosts
that should be able to reach it.

## Running locally

```bash
docker compose up -d --build
curl -X POST http://localhost:6010/translate \
  -H "Content-Type: application/json" \
  -d '{"text": "toh kaise hai aap", "target": "en"}'
```

Model weights are baked into the images at build time — `nllb-translate`
converts the NLLB checkpoint and `langid` pulls the GlotLID classifier — so
the first build is slow (several GB fetched) but a started container never
waits on a download. Check `docker compose logs -f translate langid
nllb-translate transliterate` if a request hangs or errors.

## Deploying

```bash
bash deploy.sh
```

Uses `docker compose` (v2) if installed, falling back to `docker-compose`
(v1) otherwise. Every service is built from source in this repo, so a deploy
rebuilds what changed and recreates it.

## Environment variables

None required. Every URL between the four services has a working default
(`LANGID_URL`, `TRANSLITERATE_URL`, `NLLB_URL`), since they always run
together on the same compose network — override only if you're running one
somewhere else.

`nllb-translate` also takes the knobs that decide its throughput, all with
defaults tuned for the 2-CPU budget in `docker-compose.yml`:

| Variable | Default | What it does |
| --- | --- | --- |
| `INTER_THREADS` | `2` | Translations running in parallel. Raise with the CPU quota. |
| `INTRA_THREADS` | `1` | Cores per translation. |
| `MAX_BATCH_SIZE` | `8` | Requests folded into one model call. |
| `BATCH_WINDOW_MS` | `15` | How long the first request waits for company. |
| `MAX_QUEUE_DEPTH` | `128` | Queued requests before new ones are shed with 503. |
| `JOB_TIMEOUT_SECONDS` | `25` | Wait before a queued request gives up with 504. |
