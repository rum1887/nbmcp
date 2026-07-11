//! nbmcp Rust core.
//!
//! Architecture (v0.1):
//!   - Python registers tools (name, JSON schema, callable) with `NativeEngine`.
//!   - `NativeEngine.run_stdio()` hands control to a tokio runtime that owns
//!     the MCP JSON-RPC-over-stdio transport: reading requests, validating
//!     `tools/call` arguments against the schema *in Rust*, and only then
//!     dispatching into Python.
//!   - Each tool call runs on a blocking-thread-pool task and only holds the
//!     GIL for the duration of the actual Python call; a tool that does
//!     blocking I/O (requests, sockets, subprocess) releases the GIL the
//!     same way it would in plain Python, so multiple in-flight calls can
//!     genuinely overlap instead of the Rust layer serializing them.
//!
//! This talks the MCP JSON-RPC/stdio wire protocol directly (not via the
//! `rmcp` crate) to keep the dependency surface small and the build
//! reproducible; swapping in `rmcp` later for resources/prompts/sampling
//! support is a contained change since all protocol handling lives in
//! `protocol.rs`.

mod convert;
mod protocol;
mod schema;

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;
use std::sync::Arc;

/// A single registered tool: its MCP schema plus the Python callable.
pub struct ToolEntry {
    /// Full MCP tool definition: {"name", "description", "inputSchema"}.
    pub definition: serde_json::Value,
    /// Just the inputSchema, cached out for fast validation.
    pub input_schema: serde_json::Value,
    pub func: Py<PyAny>,
}

#[pyclass]
pub struct NativeEngine {
    name: String,
    tools: HashMap<String, ToolEntry>,
    resources: Vec<serde_json::Value>,
    prompts: Vec<serde_json::Value>,
}

#[pymethods]
impl NativeEngine {
    #[new]
    fn new(name: String) -> Self {
        NativeEngine {
            name,
            tools: HashMap::new(),
            resources: Vec::new(),
            prompts: Vec::new(),
        }
    }

    /// Register a tool. `tool_def_json` is the full MCP tool definition
    /// (name/description/inputSchema) generated on the Python side from the
    /// function's type hints.
    fn register_tool(&mut self, name: String, tool_def_json: String, func: Py<PyAny>) -> PyResult<()> {
        let definition: serde_json::Value = serde_json::from_str(&tool_def_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!(
                "nbmcp: invalid tool schema for '{name}': {e}"
            )))?;
        let input_schema = definition
            .get("inputSchema")
            .cloned()
            .unwrap_or(serde_json::json!({"type": "object", "properties": {}}));
        self.tools.insert(
            name,
            ToolEntry {
                definition,
                input_schema,
                func,
            },
        );
        Ok(())
    }

    fn register_resource(&mut self, resource_json: String) -> PyResult<()> {
        let resource: serde_json::Value = serde_json::from_str(&resource_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!(
                "nbmcp: invalid resource definition: {e}"
            )))?;
        self.resources.push(resource);
        Ok(())
    }

    fn register_prompt(&mut self, prompt_json: String) -> PyResult<()> {
        let prompt: serde_json::Value = serde_json::from_str(&prompt_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!(
                "nbmcp: invalid prompt definition: {e}"
            )))?;
        self.prompts.push(prompt);
        Ok(())
    }

    /// Start the MCP stdio server. Blocks the calling Python thread until
    /// stdin closes. The GIL is released for the duration (`py.allow_threads`)
    /// so background Python threads / other interpreter activity aren't
    /// starved while the Rust event loop runs.
    fn run_stdio(&self, py: Python<'_>) -> PyResult<()> {
        let tools = Arc::new(
            self.tools
                .iter()
                .map(|(name, entry)| {
                    (
                        name.clone(),
                        ToolEntry {
                            definition: entry.definition.clone(),
                            input_schema: entry.input_schema.clone(),
                            func: entry.func.clone_ref(py),
                        },
                    )
                })
                .collect::<HashMap<_, _>>(),
        );
        let resources = Arc::new(self.resources.clone());
        let prompts = Arc::new(self.prompts.clone());
        let server_name = self.name.clone();

        py.detach(move || {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .expect("nbmcp: failed to start tokio runtime");
            runtime.block_on(protocol::serve_stdio(server_name, tools, resources, prompts))
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("nbmcp server error: {e}")))
    }

    fn run_http(&self, py: Python<'_>, address: String) -> PyResult<()> {
        let tools = Arc::new(
            self.tools
                .iter()
                .map(|(name, entry)| {
                    (
                        name.clone(),
                        ToolEntry {
                            definition: entry.definition.clone(),
                            input_schema: entry.input_schema.clone(),
                            func: entry.func.clone_ref(py),
                        },
                    )
                })
                .collect::<HashMap<_, _>>(),
        );
        let resources = Arc::new(self.resources.clone());
        let prompts = Arc::new(self.prompts.clone());
        let server_name = self.name.clone();

        py.detach(move || {
            let runtime = tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .expect("nbmcp: failed to start tokio runtime");
            runtime.block_on(protocol::serve_http(server_name, tools, resources, prompts, address))
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("nbmcp server error: {e}")))
    }

    fn __repr__(&self) -> String {
        format!("NativeEngine(name={:?}, tools={})", self.name, self.tools.len())
    }
}

/// Validate `arguments` (already-parsed JSON) against a tool's input schema.
/// Exposed for the async protocol layer.
pub(crate) fn validate_arguments(
    entry: &ToolEntry,
    arguments: &serde_json::Value,
) -> Result<(), String> {
    schema::validate(&entry.input_schema, arguments)
}

/// Call the Python tool function with the given JSON arguments, acquiring
/// the GIL only for the call itself. Returns the stringified/JSON-dumped
/// result, or an error message.
pub(crate) fn call_python_tool(
    func: &Py<PyAny>,
    arguments: &serde_json::Value,
) -> Result<String, String> {
    Python::attach(|py| {
        let kwargs = PyDict::new(py);
        if let serde_json::Value::Object(map) = arguments {
            for (k, v) in map {
                let py_val = convert::json_to_py(py, v).map_err(|e| e.to_string())?;
                kwargs
                    .set_item(k, py_val)
                    .map_err(|e| e.to_string())?;
            }
        }
        let result = func
            .call(py, (), Some(&kwargs))
            .map_err(|e| {
                // Surface the Python exception message/type, not a generic error.
                e.to_string()
            })?;
        convert::py_result_to_text(py, &result).map_err(|e| e.to_string())
    })
}

#[pymodule]
fn _nbmcp_core(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<NativeEngine>()?;
    Ok(())
}
