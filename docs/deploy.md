# Deploying monty-claw to Cloud Run

The service is stateless: the chat record (transcript, dedupe) lives in
MongoDB and the agent's message history (its memory) lives in GCS. Telegram
delivers messages via webhook; the whole agent turn runs inside the request (safe with
scale-to-zero — CPU is only guaranteed during a request; Telegram retries
unacknowledged updates and `update_id` dedupe makes that idempotent).

## Prerequisites

- A Telegram bot token from @BotFather.
- A MongoDB deployment reachable from Cloud Run (Atlas free tier works;
  allowlist `0.0.0.0/0` with TLS auth for MVP, VPC peering later).
- An OpenAI-compatible LLM endpoint (the default env targets local Ollama —
  set a hosted endpoint for prod).

## One-time setup

```bash
export PROJECT=<your-project> REGION=asia-southeast1 BUCKET=<your-bucket>

gcloud config set project $PROJECT

# Blob storage for message history
gcloud storage buckets create gs://$BUCKET --location=$REGION

# Service account
gcloud iam service-accounts create monty-claw
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:monty-claw@$PROJECT.iam.gserviceaccount.com" \
  --role=roles/storage.objectAdmin

# Secrets
printf '%s' "$TELEGRAM_BOT_TOKEN" | gcloud secrets create telegram-bot-token --data-file=-
printf '%s' "$MONGODB_URI"        | gcloud secrets create mongodb-uri --data-file=-
printf '%s' "$LLM_API_KEY"        | gcloud secrets create llm-api-key --data-file=-
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create telegram-secret-token --data-file=-
for s in telegram-bot-token mongodb-uri llm-api-key telegram-secret-token; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:monty-claw@$PROJECT.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

## Deploy

```bash
gcloud run deploy monty-claw --source . --region=$REGION \
  --service-account=monty-claw@$PROJECT.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --timeout=300 --memory=1Gi \
  --set-env-vars=STORAGE_BACKEND=gcs,GCS_BUCKET=$BUCKET,LLM_BASE_URL=<url>,LLM_MODEL=<model> \
  --set-secrets=TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,MONGODB_URI=mongodb-uri:latest,LLM_API_KEY=llm-api-key:latest,TELEGRAM_SECRET_TOKEN=telegram-secret-token:latest
```

`--memory=1Gi` because Monty runs subprocess workers. `--timeout=300` is
headroom; the engine's own `TURN_DEADLINE_SECS=50` keeps turns under
Telegram's webhook patience.

### Web UI (optional)

To enable the chat/configuration frontend at the service root, add
credentials (as secrets or env vars) to the deploy:

```bash
printf '%s' "$WEB_PASSWORD" | gcloud secrets create web-password --data-file=-
# then extend the deploy flags:
#   --set-env-vars=...,WEB_USERNAME=<you>
#   --set-secrets=...,WEB_PASSWORD=web-password:latest
```

Set `WEB_SECRET_KEY` (e.g. `openssl rand -hex 32`) as a secret too if you
want login sessions to survive password rotation.

## Point the webhook

```bash
TELEGRAM_BOT_TOKEN=... TELEGRAM_SECRET_TOKEN=... \
  uv run monty-claw set-webhook --url https://<run-url>/webhook/telegram
```

## Generated media

`generate_image` writes mock-up PNGs to the same bucket under `media/` and the
service serves them at `/media/...`, so the bucket stays private. Telegram gets
the image inline as well: the bytes are uploaded via `sendPhoto`, which works
even before `PUBLIC_BASE_URL` is set. Set it to the Cloud Run URL
(`--update-env-vars=PUBLIC_BASE_URL=https://...`) so the links the assistant
sends alongside the photo are absolute.

## Smoke test

1. Message the bot: "remember that my favorite fruit is durian".
2. Force a cold start (`gcloud run services update monty-claw --region=$REGION --update-env-vars=BUMP=1`
   or just wait for scale-to-zero).
3. Ask: "what did I ask you to remember?" — the answer proves the GCS
   message history survived instance death.

## Local development

```bash
uv run monty-claw repl                 # terminal chat; no Telegram/Mongo needed
uv run monty-claw poll                 # real Telegram via getUpdates; no public URL
```

`poll` needs `TELEGRAM_BOT_TOKEN` and a running MongoDB (`MONGODB_URI`,
default `mongodb://localhost:27017`); storage defaults to the local
`.monty_claw_state/` directory.

## If turns outgrow the 50s deadline (documented, not built)

- Run with `--no-cpu-throttling` and respond-then-background, or
- fan out through Cloud Tasks, and/or
- park mid-execution with Monty suspended-snapshot dumps and resume in a
  later request.
