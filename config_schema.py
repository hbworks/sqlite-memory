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
            default="",
            description=(
                "Optional; leave blank for the active profile's memory.db. "
                "Set only for a custom or shared database."
            ),
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
            description="Maximum number of relevant memories to inject per turn (bounded to 5–100).",
            minimum=5,
            maximum=100,
            step=1,
            inline=True,
        )
    ),
)
