from __future__ import annotations

CODE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "assistant_text": {"type": "string", "minLength": 1},
    },
    "required": ["files", "assistant_text"],
    "additionalProperties": False,
}
