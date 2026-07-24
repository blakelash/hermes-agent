"""Prospective Scientific Memory Benchmark (PSMB).

An initial prototype of a benchmark for *prospective associative memory for
discovery*: while doing ordinary new scientific work, does an agent
spontaneously recognize that several previously disconnected observations now
form a useful hypothesis -- without anyone asking it to search the past?

See ``README.md`` for the full design and the prototype's scope.
"""

from benchmarks.prospective_scientific_memory.schema import (  # noqa: F401
    Artifact,
    ArtifactKind,
    Connection,
    ConnectionClass,
    DifficultyProfile,
    Edge,
    EdgeStatus,
    Entity,
    EntityType,
    Episode,
    Evidence,
    Maturation,
    Predicate,
    Result,
    Task,
    TaskKind,
)

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Connection",
    "ConnectionClass",
    "DifficultyProfile",
    "Edge",
    "EdgeStatus",
    "Entity",
    "EntityType",
    "Episode",
    "Evidence",
    "Maturation",
    "Predicate",
    "Result",
    "Task",
    "TaskKind",
]

__version__ = "0.1.0"
