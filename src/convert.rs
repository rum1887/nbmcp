//! Conversion helpers between `serde_json::Value` and Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;

/// Convert a JSON value into a Python object.
pub fn json_to_py(py: Python<'_>, value: &Value) -> PyResult<PyObject> {
    Ok(match value {
        Value::Null => py.None(),
        Value::Bool(b) => b.into_py(py),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_py(py)
            } else if let Some(f) = n.as_f64() {
                f.into_py(py)
            } else {
                py.None()
            }
        }
        Value::String(s) => s.into_py(py),
        Value::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(json_to_py(py, item)?)?;
            }
            list.into_py(py)
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            dict.into_py(py)
        }
    })
}

/// Convert a Python object into JSON text, preferring `json.dumps` for
/// structured results (dict/list/etc) and falling back to `str()` for
/// anything that isn't JSON-serializable (e.g. a custom object a tool
/// author returned directly).
pub fn py_result_to_text(py: Python<'_>, obj: &PyObject) -> PyResult<String> {
    if let Ok(s) = obj.extract::<String>(py) {
        return Ok(s);
    }
    let json_module = py.import("json")?;
    match json_module.call_method1("dumps", (obj,)) {
        Ok(dumped) => dumped.extract::<String>(),
        Err(_) => {
            let s = obj.call_method0(py, "__str__")?;
            s.extract::<String>(py)
        }
    }
}
