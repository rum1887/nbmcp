//! Conversion helpers between `serde_json::Value` and Python objects.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::IntoPyObjectExt;
use serde_json::Value;

/// Convert a JSON value into a Python object.
pub fn json_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => b.into_py_any(py),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_py_any(py)
            } else if let Some(f) = n.as_f64() {
                f.into_py_any(py)
            } else {
                Ok(py.None())
            }
        }
        Value::String(s) => s.into_py_any(py),
        Value::Array(items) => {
            let converted: Vec<Py<PyAny>> = items
                .iter()
                .map(|item| json_to_py(py, item))
                .collect::<PyResult<_>>()?;
            Ok(PyList::new(py, converted)?.into_any().unbind())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}

/// Convert a Python object into JSON text, preferring `json.dumps` for
/// structured results (dict/list/etc) and falling back to `str()` for
/// anything that isn't JSON-serializable (e.g. a custom object a tool
/// author returned directly).
pub fn py_result_to_text(py: Python<'_>, obj: &Py<PyAny>) -> PyResult<String> {
    if let Ok(s) = obj.extract::<String>(py) {
        return Ok(s);
    }
    let json_module = py.import("json")?;
    if let Ok(dumped) = json_module.call_method1("dumps", (obj.bind(py),)) {
        return dumped.extract::<String>();
    }
    let s = obj.bind(py).str()?;
    Ok(s.to_str()?.to_string())
}
