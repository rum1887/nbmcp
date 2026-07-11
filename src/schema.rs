//! Minimal JSON Schema validator.
//!
//! nbmcp generates schemas from Python type hints on the Python side and
//! validates incoming tool-call arguments against them here, in Rust,
//! *before* the call ever crosses into the Python interpreter. This is
//! deliberately a small hand-rolled subset of JSON Schema (object/type/
//! required/items/properties) rather than a full-spec validator: it covers
//! everything nbmcp's schema generator actually emits, with no extra
//! dependency / MSRV risk. It can be swapped for a full `jsonschema` crate
//! later without changing the public API.

use serde_json::Value;

/// Validate `instance` against `schema`. Returns Ok(()) or a human-readable
/// error describing the first mismatch found.
pub fn validate(schema: &Value, instance: &Value) -> Result<(), String> {
    validate_node(schema, instance, "$")
}

fn validate_node(schema: &Value, instance: &Value, path: &str) -> Result<(), String> {
    let Some(schema_obj) = schema.as_object() else {
        // No constraints specified for this node.
        return Ok(());
    };

    if let Some(Value::String(expected_type)) = schema_obj.get("type") {
        check_type(expected_type, instance, path)?;
    }

    match instance {
        Value::Object(instance_obj) => {
            if let Some(Value::Array(required)) = schema_obj.get("required") {
                for req in required {
                    if let Value::String(key) = req {
                        if !instance_obj.contains_key(key) {
                            return Err(format!(
                                "{path}: missing required field \"{key}\""
                            ));
                        }
                    }
                }
            }
            if let Some(Value::Object(properties)) = schema_obj.get("properties") {
                for (key, sub_schema) in properties {
                    if let Some(value) = instance_obj.get(key) {
                        validate_node(sub_schema, value, &format!("{path}.{key}"))?;
                    }
                }
            }
        }
        Value::Array(items) => {
            if let Some(item_schema) = schema_obj.get("items") {
                for (i, item) in items.iter().enumerate() {
                    validate_node(item_schema, item, &format!("{path}[{i}]"))?;
                }
            }
        }
        _ => {}
    }

    Ok(())
}

fn check_type(expected: &str, instance: &Value, path: &str) -> Result<(), String> {
    let matches = match expected {
        "string" => instance.is_string(),
        "integer" => instance.is_i64() || instance.is_u64(),
        "number" => instance.is_number(),
        "boolean" => instance.is_boolean(),
        "array" => instance.is_array(),
        "object" => instance.is_object(),
        "null" => instance.is_null(),
        _ => true, // unknown type keyword: don't block on it
    };
    if matches {
        Ok(())
    } else {
        Err(format!(
            "{path}: expected type \"{expected}\", got {}",
            value_type_name(instance)
        ))
    }
}

fn value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn required_field_missing() {
        let schema = json!({
            "type": "object",
            "properties": { "city": { "type": "string" } },
            "required": ["city"]
        });
        let err = validate(&schema, &json!({})).unwrap_err();
        assert!(err.contains("city"));
    }

    #[test]
    fn wrong_type() {
        let schema = json!({
            "type": "object",
            "properties": { "count": { "type": "integer" } },
            "required": ["count"]
        });
        let err = validate(&schema, &json!({"count": "five"})).unwrap_err();
        assert!(err.contains("integer"));
    }

    #[test]
    fn valid_passes() {
        let schema = json!({
            "type": "object",
            "properties": {
                "city": { "type": "string" },
                "days": { "type": "array", "items": { "type": "integer" } }
            },
            "required": ["city"]
        });
        assert!(validate(&schema, &json!({"city": "Bengaluru", "days": [1, 2, 3]})).is_ok());
    }
}
