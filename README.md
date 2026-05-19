# grok2api

`grok2api` is a local OpenAI-compatible API proxy for Grok that uses your own xAI OAuth login. It is designed for users who already have access to Grok/xAI features and want a local `/v1` API surface for OpenAI-compatible clients.

This project is not affiliated with xAI, Grok, OpenAI, or NousResearch.

## What it does

- Opens an xAI OAuth authorization-code + PKCE login flow.
- Stores your tokens locally under your user config directory.
- Refreshes access tokens when possible.
- Proxies requests to `https://api.x.ai/v1`.
- Exposes OpenAI-style endpoints locally:
  - `GET /v1/models`
  - `POST /v1/responses`
  - `POST /v1/chat/completions`
- Streams Server-Sent Events without rewriting event order.

## What it does not do

`grok2api` does not bypass subscriptions, quotas, rate limits, API access controls, or account entitlements. If xAI returns a quota, subscription, authentication, or entitlement error, this proxy returns that upstream response.

Users are responsible for complying with xAI's terms and any terms that apply to their account or subscription.

## Install

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Login

```bash
grok2api login
```

The CLI opens your browser, starts a loopback callback server on `127.0.0.1`, and stores the OAuth tokens locally after xAI redirects back.

For a remote or headless machine:

```bash
grok2api login --headless
```

Open the printed URL on a machine with a browser, then paste the final redirected callback URL when prompted.

## Start the API server

```bash
grok2api serve --host 127.0.0.1 --port 8000
```

The server binds to localhost by default. To bind to a non-loopback address, you must pass `--allow-remote` and configure a local proxy API key:

```bash
GROK2API_API_KEY=change-me grok2api serve --host 0.0.0.0 --allow-remote
```

Never expose the proxy publicly without an API key and network-level controls. Anyone who can reach the proxy can spend your xAI account quota.

## Responses API example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-anything",
)

response = client.responses.create(
    model="grok-3-mini",
    input="Say hello from Grok through grok2api",
)

print(response.output_text)
```

## Streaming example

```bash
curl -N http://127.0.0.1:8000/v1/responses \
  -H "Authorization: Bearer local-anything" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-3-mini","input":"Count to three","stream":true}'
```

## Chat Completions example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-anything",
)

completion = client.chat.completions.create(
    model="grok-3-mini",
    messages=[{"role": "user", "content": "Hello"}],
)

print(completion.choices[0].message.content)
```

`/v1/chat/completions` is proxied directly to xAI's chat-completions endpoint. Prefer `/v1/responses` for new applications.

## Other commands

```bash
grok2api models
grok2api whoami
grok2api logout
```

`whoami` displays non-sensitive local token metadata only; it never prints access or refresh tokens.

## Configuration

Environment variables:

| Variable | Purpose |
| --- | --- |
| `GROK2API_XAI_CLIENT_ID` | OAuth client ID. Defaults to the observed Grok/Hermes public client ID. |
| `GROK2API_XAI_SCOPE` | OAuth scope. |
| `GROK2API_XAI_API_BASE_URL` | Upstream API base URL. Defaults to `https://api.x.ai/v1`. |
| `GROK2API_XAI_AUTHORIZATION_URL` | OAuth authorization endpoint. |
| `GROK2API_XAI_TOKEN_URL` | OAuth token endpoint. |
| `GROK2API_REDIRECT_PORT` | Preferred loopback redirect port. Defaults to `56121`. |
| `GROK2API_API_KEY` | Optional local proxy API key; required for remote binding. |

The built-in OAuth client ID matches the observed Grok/Hermes OAuth flow. It is not an entitlement bypass; xAI still decides whether the authenticated account can use each model or API feature.

## Troubleshooting

### Login expired or refresh failed

Run:

```bash
grok2api login
```

### Callback port is busy

The CLI tries the preferred port first and can fall back to an OS-assigned loopback port. You can also set another preferred port:

```bash
GROK2API_REDIRECT_PORT=56122 grok2api login
```

### Subscription, quota, or entitlement errors

Those errors come from xAI and are intentionally passed through unchanged. Check your account, subscription, model access, and xAI's current API availability.

### Headless server

Use:

```bash
grok2api login --headless
```

Paste either the full redirected callback URL or the authorization code when prompted.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```
