"""SQLite Memory declared config surface for Hermes Agent desktop / web UI."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from plugins.memory.config_schema import (
    KIND_BOOL,
    KIND_NUMBER,
    KIND_TEXT,
    ProviderConfigSchema,
    ProviderField,
)


def _load_config_field_definitions():
    try:
        from .config_fields import CONFIG_FIELD_DEFINITIONS
        return CONFIG_FIELD_DEFINITIONS
    except ImportError:
        # Hermes loads provider schemas by file path, outside the provider package.
        fields_path = Path(__file__).with_name("config_fields.py")
        module_name = f"_sqlite_memory_config_fields_{abs(hash(fields_path.resolve()))}"
        spec = spec_from_file_location(module_name, fields_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load SQLite memory config fields from {fields_path}")
        module = module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.CONFIG_FIELD_DEFINITIONS


CONFIG_FIELD_DEFINITIONS = _load_config_field_definitions()


_FIELD_KINDS = {
    "bool": KIND_BOOL,
    "number": KIND_NUMBER,
    "text": KIND_TEXT,
}

CONFIG_SCHEMA = ProviderConfigSchema(
    name="sqlite-memory",
    label="SQLite Persistent Memory",
    fields=tuple(
        ProviderField(
            key=field.key,
            label=field.label,
            kind=_FIELD_KINDS[field.kind],
            default=field.default,
            description=field.description,
            inline=field.inline,
        )
        for field in CONFIG_FIELD_DEFINITIONS
    ),
)
