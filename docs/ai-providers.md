# AI providers and models

Axon runs local models through Ollama by default. You can also use any cloud or
company model: OpenAI, Anthropic, Google Gemini, OpenRouter, or any
OpenAI-compatible endpoint (Azure OpenAI, Groq, Mistral, Together, LM Studio,
vLLM, LiteLLM, a company gateway).

Open **Axon Settings → AI Models** (or run `axon-settings --ai-models`).

- **Models** picks the model for each tier: *Everyday* (chat), *Fast*
  (commands, window sorting) and *Deep* (code, long reasoning). The list shows
  every model from every provider that is set up.
- **Providers** is where you paste an API key, sign in, or add your own
  endpoint with the **+** button.

## Model names

Every Brain D-Bus method that takes a model accepts:

| Reference | Meaning |
|---|---|
| `qwen2.5:7b` | Local Ollama model (unchanged behaviour) |
| `@openai/gpt-4.1` | Model `gpt-4.1` from provider `openai` |
| `@openrouter/anthropic/claude-sonnet-4.5` | Anything after the first `/` is the provider's own model name |
| `@work/gpt-4o` | A custom provider you added with ID `work` |

`ListAllModels(refresh)` returns all of them, and the AI Panel's model menu uses it.

## Signing in (OAuth)

Sign-in is offered only where the provider allows third-party apps to sign
users in:

| Provider | How |
|---|---|
| OpenRouter | Works out of the box. Opens OpenRouter in the browser and creates a key for Axon (PKCE). |
| Google Gemini | Browser sign-in once an OAuth **Desktop app** client ID (and its client secret) from Google Cloud is entered with **Edit**. |
| Company identity (Microsoft Entra ID, Okta, Auth0, Keycloak …) | Add a provider, choose *Browser sign-in* or *Device code sign-in*, and enter the issuer URL, client ID and scopes from your IT team. Endpoints are discovered from `<issuer>/.well-known/openid-configuration`. |
| OpenAI, Anthropic | API key only: neither offers sign-in for third-party apps. |

Example for Azure OpenAI with Entra ID:

- API type: OpenAI-compatible, base URL `https://<resource>.openai.azure.com/openai/v1`
- Sign-in: Device code, issuer `https://login.microsoftonline.com/<tenant-id>/v2.0`
- Scopes: `https://cognitiveservices.azure.com/.default offline_access`
- Client ID: a public-client app registration from your tenant

Access tokens are refreshed automatically with the refresh token. If refresh
fails, Axon asks you to sign in again.

## Where secrets live

- API keys and OAuth tokens: the GNOME keyring (Secret Service, via libsecret),
  one item per provider labelled *Axon AI provider: &lt;id&gt;*. They never pass
  back over D-Bus.
- Provider settings (no secrets): `~/.local/share/axon/providers.json`.
- Tier choices: `~/.local/share/axon/config.toml`.
- Environment variables `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`
  and `OPENROUTER_API_KEY` are used when no key is saved (handy in Docker),
  only while that provider still points at its official URL.

Safety rules the Brain enforces:

- Remote endpoints must use HTTPS. Plain HTTP is allowed only for loopback
  and private-network addresses (local Ollama, LM Studio, a LAN server).
- Changing a provider's URL or sign-in settings deletes its saved key and
  tokens, so a stored secret can never be redirected to another server.
- Custom providers cannot read the environment-variable keys.

## D-Bus API (org.axonos.Brain)

| Method | Purpose |
|---|---|
| `ListProviders() → s` | Providers and their state (`has_api_key`, `signed_in`, `oauth_ready` …) |
| `ListAllModels(b refresh) → s` | `{"models": [{id, name, provider, provider_label}], "errors": {provider: message}}` |
| `GetModelSettings() → s` / `SetModel(s tier, s model) → s` | Read or set `speed`, `general`, `deep` or `all` |
| `SaveProvider(s json) → s` / `RemoveProvider(s id) → s` | Add, edit, delete (custom) or reset (built-in) |
| `SetApiKey(s id, s key) → s` | Save a key; an empty key removes it |
| `StartSignIn(s id)`, `GetSignInStatus(s id)`, `CancelSignIn(s id)`, `SignOut(s id)` | OAuth sign-in; the `SignInStatus(s id, s state, s info)` signal reports `awaiting_user`, `done` or `error` |

Embeddings (`GetEmbeddings`) and model downloads (`PullModel`) stay on Ollama.
