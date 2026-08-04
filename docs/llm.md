# Optional LLM layer

Checks, scores, and checklist answers remain deterministic and offline. With
a configured provider (`ADDUCE_LLM_PROVIDER=openai|anthropic|ollama`, bring
your own key or a local model), `adduce checklist --llm` can draft optional
free-text justification from finding summaries. Provider prose is labelled as
unverified, carries an author-review marker, and never counts as evidence or
determines the ledger answer. The ledger records the provider, model, and a
hash of each prose fragment without recording credentials. Without a
provider, everything works identically. Adduce ships no key and makes no
provider request unless the user explicitly selects `--llm` and configures a
provider.
