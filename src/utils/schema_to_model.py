# from typing import Any, Dict, List, Optional, Union, Literal

# from pydantic import BaseModel
# from pydantic import Field
# from pydantic import create_model


# def transform(schema: dict, definitions: dict = None) -> BaseModel:
#     title = schema.get("title", "DynamicModel")
#     description = schema.get("description", None)

#     # top level schema provides definitions
#     if definitions is None:
#         if "$defs" in schema:
#             definitions = schema["$defs"]
#         elif "definitions" in schema:
#             definitions = schema["definitions"]
#         else:
#             definitions = {}

#     def convert_type(prop: dict) -> Any:
#         if "$ref" in prop:
#             ref_path = prop["$ref"].split("/")
#             ref = definitions[ref_path[-1]]
#             return transform(ref, definitions)

#         if "enum" in prop:
#             enum_values = tuple(prop["enum"])
#             return Literal.__getitem__(enum_values)

#         if "type" in prop:
#             type_mapping = {
#                 "string": str,
#                 "number": float,
#                 "integer": int,
#                 "boolean": bool,
#                 "array": List,
#                 "object": Dict[str, Any],
#                 "null": type(None),
#             }

#             type_ = prop["type"]

#             if type_ == "array":
#                 return List[convert_type(prop.get("items", {}))]
#             elif type_ == "object":
#                 if "properties" in prop:
#                     return transform(prop, definitions)
#                 else:
#                     return Dict[str, Any]
#             else:
#                 return type_mapping.get(type_, Any)

#         elif "allOf" in prop:
#             combined_fields = {}
#             for sub_schema in prop["allOf"]:
#                 model = transform(sub_schema, definitions)
#                 combined_fields.update(model.__annotations__)
#             return create_model("CombinedModel", **combined_fields)

#         elif "anyOf" in prop:
#             sub_types = [convert_type(sub) for sub in prop["anyOf"]]
#             if any(t == type(None) for t in sub_types):
#                 non_null_types = [t for t in sub_types if t != type(None)]
#                 if len(non_null_types) == 1:
#                     return Optional[non_null_types[0]]
#                 else:
#                     return Optional[Union[tuple(non_null_types)]]
#             return Union[tuple(sub_types)]

#         elif prop == {} or "type" not in prop:
#             return Any

#         raise ValueError(f"Unsupported schema: {prop}")

#     fields = {}
#     required_fields = schema.get("required", [])

#     for name, prop in schema.get("properties", {}).items():
#         pydantic_type = convert_type(prop)
#         field_kwargs = {}
#         if "default" in prop:
#             field_kwargs["default"] = prop["default"]
#         if name not in required_fields:
#             pydantic_type = Optional[pydantic_type]
#             if "default" not in field_kwargs:
#                 field_kwargs["default"] = None
#         if "description" in prop:
#             field_kwargs["description"] = prop["description"]

#         fields[name] = (pydantic_type, Field(**field_kwargs))

#     model = create_model(title, **fields)
#     if description:
#         model.__doc__ = description
#     return model


from pydantic import create_model, BaseModel, Field
from typing import Any, Dict, List, Optional, Union, Literal, ForwardRef
from functools import lru_cache


def transform(schema: dict, definitions: dict = None, _registry: dict = None) -> BaseModel:
    if _registry is None:
        _registry = {}

    title = schema.get("title", "DynamicModel")
    if title in _registry:
        return _registry[title]

    # Create temporary forward ref
    _registry[title] = ForwardRef(title)

    description = schema.get("description", None)

    # top level schema provides definitions
    if definitions is None:
        if "$defs" in schema:
            definitions = schema["$defs"]
        elif "definitions" in schema:
            definitions = schema["definitions"]
        else:
            definitions = {}

    def convert_type(prop: dict) -> Any:
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            if ref_name == title:  # Self-reference
                return _registry[title]
            return transform(definitions[ref_name], definitions, _registry)

        if "enum" in prop:
            enum_values = tuple(prop["enum"])
            return Literal.__getitem__(enum_values)

        if "type" in prop:
            type_mapping = {
                "string": str,
                "number": float,
                "integer": int,
                "boolean": bool,
                "array": List,
                "object": Dict[str, Any],
                "null": type(None),
            }

            type_ = prop["type"]

            if type_ == "array":
                return List[convert_type(prop.get("items", {}))]
            elif type_ == "object":
                if "properties" in prop:
                    return transform(prop, definitions)
                else:
                    return Dict[str, Any]
            else:
                return type_mapping.get(type_, Any)

        elif "allOf" in prop:
            combined_fields = {}
            for sub_schema in prop["allOf"]:
                model = transform(sub_schema, definitions)
                combined_fields.update(model.__annotations__)
            return create_model("CombinedModel", **combined_fields)

        elif "anyOf" in prop:
            unioned_types = tuple(convert_type(sub_schema) for sub_schema in prop["anyOf"])
            return Union[unioned_types]  # type: ignore

        elif prop == {} or "type" not in prop:
            return Any

        raise ValueError(f"Unsupported schema: {prop}")

    fields = {}
    required_fields = schema.get("required", [])

    for name, prop in schema.get("properties", {}).items():
        pydantic_type = convert_type(prop)
        field_kwargs = {}
        if "default" in prop:
            field_kwargs["default"] = prop["default"]
        if name not in required_fields:
            pydantic_type = Optional[pydantic_type]
            if "default" not in field_kwargs:
                field_kwargs["default"] = None
        if "description" in prop:
            field_kwargs["description"] = prop["description"]

        fields[name] = (pydantic_type, Field(**field_kwargs))

    model = create_model(title, **fields)
    if description:
        model.__doc__ = description
    _registry[title] = model
    model.model_rebuild()
    return model
