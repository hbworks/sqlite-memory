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
    label="Persistent Memory (SQLite)",
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
            description="Save explicit English/Japanese remember requests and reply preferences from the first non-empty line as category preference.",
            inline=True,
        ),
        ProviderField(
            key="max_recall",
            label="Max Recall Results",
            kind=KIND_NUMBER,
            default="5",
            description="Maximum number of relevant memories to inject per turn (bounded to 5–100).",
            inline=True,
        )
    ),
)
