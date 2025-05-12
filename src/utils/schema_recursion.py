from typing import Any


def break_cycles(
    schema: Any,
    root_schema: dict | None = None,
    max_cycles: int = 2,
    ref_counts: dict[str, int] | None = None,
) -> Any:
    if root_schema is None:
        root_schema = schema
    if not isinstance(schema, (dict, list)):
        return schema
    ref_counts = ref_counts or {}
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        cnt = ref_counts.get(ref, 0) + 1
        if cnt > max_cycles:
            return {"type": "object", "properties": {"cycle_break": {"type": "string"}}}
        ref_counts[ref] = cnt
        return break_cycles(resolve_ref(ref, root_schema), root_schema, max_cycles, ref_counts)
    if isinstance(schema, dict):
        return {k: break_cycles(v, root_schema, max_cycles, ref_counts) for k, v in schema.items()}
    return [break_cycles(i, root_schema, max_cycles, ref_counts) for i in schema]


def resolve_ref(ref: str, root_schema: dict) -> dict:
    parts = ref.lstrip("#/").split("/")
    t = root_schema
    for p in parts:
        t = t[p]
    return t
