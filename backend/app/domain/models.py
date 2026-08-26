from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EngineKind(str, Enum):
    SEMANTIC = 'semantic'
    SEMANTIC_FACT = 'semantic_fact'
    ABBREVIATION_FACT_MAP = 'abbreviation_fact_map'
    CANDIDATE = 'candidate'
    DETERMINISTIC = 'deterministic'
    STRUCTURAL = 'structural'
    MANUAL = 'manual'
    UNAVAILABLE = 'unavailable'


class RoutingSpecModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    strategy: str
    selectors: list[str] = Field(default_factory=list)
    exhaustive: bool = False
    allowPass: bool = True
    reason: str | None = None
    requiresArtifacts: list[str] = Field(default_factory=list)
    requiresCompleteSelectors: list[str] = Field(default_factory=list)
    onMissingPrerequisite: str | None = None
    candidateFamily: str | None = None
    detectorId: str | None = None


class RuleEngineModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    kind: EngineKind
    detectorId: str | None = None
    candidateFamily: str | None = None
    facts: list[str] = Field(default_factory=list)
    guidance: str | None = None
    externalKnowledge: str | None = None
    fixPolicy: str | None = None
    dedupGroup: str | None = None
    sharedFactsFrom: str | None = None
    factNameMap: dict[str, str] = Field(default_factory=dict)
    inventory: str | None = None
    globalFactKeys: list[str] = Field(default_factory=list)


class RuleManifestEntryModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    layer: Literal['core', 'soft']
    sourceNumber: str
    scope: str
    severity: Literal['critical', 'major', 'minor', 'info']
    weight: float
    mode: Literal['semantic', 'candidate', 'deterministic', 'structural', 'manual']
    dedupKey: str
    engine: RuleEngineModel
    routing: RoutingSpecModel
    applicability: dict[str, Any] | None = None


class RuleManifestModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: int
    description: str = ''
    rules: dict[str, RuleManifestEntryModel]


class SemanticSectionModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str
    type: str
    label: str
    startBlockId: str
    endBlockId: str
    blockIds: list[str] = Field(default_factory=list)
    complete: bool = False
    state: str = 'ambiguous'
    canonicalRole: str | None = None


class SemanticRelationModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    type: str
    statementIndex: int | None = None
    sourceSectionId: str | None = None
    targetSectionId: str | None = None
    targetStartBlockId: str | None = None
    role: str = 'primary'
    confidence: float = 0.0
    state: Literal['confirmed', 'ambiguous'] = 'ambiguous'
    reason: str = ''
    source: str = 'document_map'


class SemanticDocumentModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    version: int = 1
    sections: list[SemanticSectionModel] = Field(default_factory=list)
    relations: list[SemanticRelationModel] = Field(default_factory=list)
    defenseStatements: list[dict[str, Any]] = Field(default_factory=list)
    relationSource: str = 'document_map'


class FragmentModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: str
    type: str
    selector: str
    label: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    complete: bool = False


class RoutedRuleModel(BaseModel):
    model_config = ConfigDict(extra='allow')

    rule: dict[str, Any]
    strategy: str
    fragmentIds: list[str] = Field(default_factory=list)
    exhaustive: bool = False
    allowPass: bool = True
    reason: str | None = None
    explicit: bool = True
    candidateFamily: str | None = None
    detectorId: str | None = None


class RuleResultModel(BaseModel):
    """Typed boundary for rule results while legacy checkers still emit dictionaries."""

    model_config = ConfigDict(extra='allow')

    ruleId: str
    status: str
    severity: str = 'major'
    explanation: str = ''
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidenceStatus: str = 'not_required'
    checkedBy: str = 'system'
