# PMI Member Analysis API

Cloudflare deployment scaffold for a GPT Action endpoint at:

```text
https://api.energysaverai.uk/pmi/member-analysis
```

The Worker validates `x-api-key` against the `ACTION_API_KEY` secret, removes that header before forwarding, and proxies the request body to a Cloudflare Container without reading or logging uploaded content. The Python/FastAPI container downloads the GPT Action `openaiFileIdRefs` PDF into memory, loads the Excel template from R2 through the S3-compatible API, and returns a completed workbook in `openaiFileResponse`.

## Privacy controls

- Uploaded PDFs are downloaded/read into memory only and are not persisted.
- Uvicorn access logging is disabled.
- The Worker does not inspect, parse, or log the request body.
- The container logs operational events only; it does not log document contents, member names, dates of birth, premiums, or medical/insurance details.
- Responses use `Cache-Control: no-store`.
- `ACTION_TIMEOUT_MS` defaults to `42000` so the API returns before the 45-second GPT Action timeout.

## R2 template setup

Create an R2 bucket for the blank Excel template. If available for your account, choose a Western Europe location hint (`weur`) or an EU jurisdictional option from the Cloudflare dashboard.

Upload the template at:

```text
templates/pmi-member-analysis-template.xlsx
```

The container expects these environment variables:

```text
R2_BUCKET
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_TEMPLATE_KEY
```

`R2_TEMPLATE_KEY` defaults to `templates/pmi-member-analysis-template.xlsx`.

## Deploy

Install dependencies:

```bash
npm install
```

Set the GPT Action API key and the R2 S3 credentials. `ACTION_API_KEY` is the secret ChatGPT sends in the `x-api-key` header; the two R2 values let the Python container load the template directly from R2.

```bash
npx wrangler secret put ACTION_API_KEY
npx wrangler secret put R2_ACCESS_KEY_ID
npx wrangler secret put R2_SECRET_ACCESS_KEY
```

Update `wrangler.jsonc`:

- The Worker route is configured for `api.energysaverai.uk`.
- Replace `R2_BUCKET` and `R2_ACCOUNT_ID`.
- Adjust `max_instances` if expected concurrency is higher.
- Keep the route path as `/pmi/member-analysis`.

Deploy:

```bash
npm run deploy
```

Use [docs/openapi.yaml](docs/openapi.yaml) as the GPT Action schema and configure the Action authentication header as `x-api-key`.

## Local container run

```bash
docker build -t pmi-member-analysis ./container
docker run --rm -p 8080:8080 \
  -e R2_BUCKET=your-bucket \
  -e R2_ACCOUNT_ID=your-account-id \
  -e R2_ACCESS_KEY_ID=your-r2-access-key \
  -e R2_SECRET_ACCESS_KEY=your-r2-secret \
  pmi-member-analysis
```

Then post a PDF directly for local testing:

```bash
curl -X POST http://localhost:8080/pmi/member-analysis \
  -H "x-action-authenticated: true" \
  -F "pdf=@example.pdf"
```
