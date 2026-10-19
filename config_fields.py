"""Dependency-free definitions for SQLite memory provider settings."""

from typing import NamedTuple


class ConfigFieldDefinition(NamedTuple):
    key: str
    label: str
    kind: str
    default: str
    description: str
    inline: bool = True


CONFIG_SECTION_KEYS = ("sqlite-memory", "sqlite_memory")

CONFIG_FIELD_DEFINITIONS = (
    ConfigFieldDefinition(
        key="db_path",
        label="Database Path",
        kind="text",
        default="~/.hermes/memory.db",
        description="File path for the SQLite database storing long-term memories.",
    ),
    ConfigFieldDefinition(
        key="auto_extract",
        label="Auto Extract",
        kind="bool",
        default="true",
        description="Automatically extract rules and preferences from conversation turns.",
    ),
    ConfigFieldDefinition(
        key="max_recall",
        label="Max Recall Results",
        kind="number",
        default="5",
        description="Maximum number of relevant memories to inject per turn.",
    ),
)
