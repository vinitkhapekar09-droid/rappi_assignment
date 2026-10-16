"""Adapter implementations behind the agent ports.

- :mod:`app.agent.adapters.reference_domain` — a deterministic reference
  implementation of the domain rules, used so the agent and its tests run
  offline until the backend domain service is wired in.
- :mod:`app.agent.adapters.inmemory` — in-memory doubles for evidence, approval,
  execution, and validation, including forced failure and mismatch modes.

These are not the authoritative business rules; the backend owns those. They
exist to keep the agent module independently testable.
"""
