"""Dependency container injected into the workflow.

Bundles the port implementations so the graph and nodes stay free of
construction concerns and are trivial to test with doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.config import AgentConfig
from app.agent.ports import (
    ApprovalService,
    DomainService,
    EventRecorder,
    EvidenceProvider,
    ExecutionService,
    LLMClient,
    ValidationService,
)


@dataclass
class AgentDependencies:
    evidence: EvidenceProvider
    domain: DomainService
    approvals: ApprovalService
    execution: ExecutionService
    validation: ValidationService
    llm: LLMClient
    recorder: EventRecorder | None = None
    config: AgentConfig = field(default_factory=AgentConfig)
