# Integrations

ADE uses the official OpenAI Responses API as its only remote AI integration. Every request uses
`gpt-6-sol` with medium reasoning for extraction and optional verification or repair. Requests use
structured output, official HTTPS OpenAI endpoints, bounded retries, and `store=False`. Streamlit
provides the local UI. The app has no database, identity provider, queue, or alternate model provider.

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
