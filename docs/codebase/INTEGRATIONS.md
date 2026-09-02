# Integrations

The only AI integration is the official OpenAI Responses API. The active models are
`gpt-5.6-terra` medium and `gpt-5.6-sol` low. Requests use structured outputs,
official HTTPS OpenAI endpoints, bounded retries, and `store=False`. Streamlit provides
the local UI; no database, identity provider, queue, or alternate model provider is present.

## Evidence

- Model allow-list: `MODEL_CASCADE` in `src/ade_app/constants.py`.
- Official-host validation and client construction: `create_openai_responses` in
  `src/ade_app/openai_client.py`.
- Structured primary, independent, and field-resolution calls: `OpenAIPageExtractor` in
  `src/ade_app/openai_client.py`.
- Local-only server guard: the `is_loopback_address` startup check in `streamlit_app.py`.

## Checkpoints

- Every provider request must use `/v1/responses`, structured parsing, and `store=False`.
- Adding a model, provider, database, or remote listener is an architecture change, not configuration.
- Never place an API key in source, logs, manifests, or documentation examples.
