"""SQLite Memory declared config surface for Hermes Agent desktop / web UI."""

from plugins.memory.config_schema import (
    KIND_BOOL,
    KIND_NUMBER,
    KIND_TEXT,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="sqlite-memory",
    label="SQLite Persistent Memory",
    fields=(
        ProviderField(
            key="db_path",
            label="Database Path",
            kind=KIND_TEXT,
            default="~/.hermes/memory.db",
            description="File path for the SQLite database storing long-term memories.",
            inline=True,
        ),
        ProviderField(
            key="auto_extract",
            label="Auto Extract",
            kind=KIND_BOOL,
            default="true",
            description="Automatically extract rules and preferences from conversation turns.",
            inline=True,
        ),
        ProviderField(
            key="max_recall",
            label="Max Recall Results",
            kind=KIND_NUMBER,
            default="5",
            description="Maximum number of relevant memories to inject per turn.",
            inline=True,
        ),
    ),
)
