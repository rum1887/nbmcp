//! MCP JSON-RPC-over-stdio transport.
//!
//! Reads newline-delimited JSON-RPC requests from stdin, dispatches them,
//! and writes newline-delimited JSON-RPC responses to stdout. Each request
//! is handled on its own tokio task so that a tool call blocking on Python
//! I/O doesn't stall the reader loop or other in-flight calls.

use crate::{call_python_tool, validate_arguments, ToolEntry};
use bytes::Bytes;
use hyper::body::to_bytes;
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::convert::Infallible;
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{broadcast, Mutex as AsyncMutex};
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;

const PROTOCOL_VERSION: &str = "2024-11-05";

pub async fn serve_stdio(
    server_name: String,
    tools: Arc<HashMap<String, ToolEntry>>,
    resources: Arc<Vec<Value>>,
    prompts: Arc<Vec<Value>>,
) -> Result<(), String> {
    let stdin = tokio::io::stdin();
    let stdout = Arc::new(AsyncMutex::new(tokio::io::stdout()));
    let mut lines = BufReader::new(stdin).lines();

    loop {
        let line = lines
            .next_line()
            .await
            .map_err(|e| format!("stdin read error: {e}"))?;
        let Some(line) = line else {
            break; // EOF: client disconnected.
        };
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }

        let tools = Arc::clone(&tools);
        let resources = Arc::clone(&resources);
        let prompts = Arc::clone(&prompts);
        let stdout = Arc::clone(&stdout);
        let server_name = server_name.clone();

        tokio::spawn(async move {
            if let Some(response) = handle_message(&server_name, &tools, &resources, &prompts, None, &line) {
                let mut out = stdout.lock().await;
                let _ = out.write_all(response.to_string().as_bytes()).await;
                let _ = out.write_all(b"\n").await;
                let _ = out.flush().await;
            }
        });
    }

    Ok(())
}

pub async fn serve_http(
    server_name: String,
    tools: Arc<HashMap<String, ToolEntry>>,
    resources: Arc<Vec<Value>>,
    prompts: Arc<Vec<Value>>,
    address: String,
) -> Result<(), String> {
    let (event_sender, _) = broadcast::channel::<String>(128);
    let make_svc = make_service_fn(move |_conn| {
        let tools = Arc::clone(&tools);
        let resources = Arc::clone(&resources);
        let prompts = Arc::clone(&prompts);
        let server_name = server_name.clone();
        let event_sender = event_sender.clone();
        async move {
            Ok::<_, Infallible>(service_fn(move |req| {
                handle_http_request(
                    req,
                    server_name.clone(),
                    Arc::clone(&tools),
                    Arc::clone(&resources),
                    Arc::clone(&prompts),
                    event_sender.clone(),
                )
            }))
        }
    });

    let addr = address.parse().map_err(|e| format!("invalid address: {e}"))?;
    let server = Server::bind(&addr).serve(make_svc);

    server.await.map_err(|e| format!("HTTP server error: {e}"))
}

async fn handle_http_request(
    req: Request<Body>,
    server_name: String,
    tools: Arc<HashMap<String, ToolEntry>>,
    resources: Arc<Vec<Value>>,
    prompts: Arc<Vec<Value>>,
    event_sender: broadcast::Sender<String>,
) -> Result<Response<Body>, Infallible> {
    match (req.method(), req.uri().path()) {
        (&Method::POST, "/") | (&Method::POST, "/jsonrpc") => {
            let bytes: Bytes = match to_bytes(req.into_body()).await {
                Ok(bytes) => bytes,
                Err(_) => {
                    return Ok(Response::builder()
                        .status(StatusCode::BAD_REQUEST)
                        .body(Body::from("Invalid request body"))
                        .unwrap())
                }
            };
            let line = std::str::from_utf8(&bytes).unwrap_or("");
            let response = match handle_message(
                &server_name,
                &tools,
                &resources,
                &prompts,
                Some(&event_sender),
                line,
            ) {
                Some(value) => {
                    let text = value.to_string();
                    Response::builder()
                        .status(StatusCode::OK)
                        .header("content-type", "application/json")
                        .body(Body::from(text))
                        .unwrap()
                }
                None => Response::builder()
                    .status(StatusCode::NO_CONTENT)
                    .body(Body::empty())
                    .unwrap(),
            };
            Ok(response)
        }
        (&Method::GET, "/events") => {
            let receiver = event_sender.subscribe();
            let event_stream = BroadcastStream::new(receiver).filter_map(|result| {
                match result {
                    Ok(message) => Some(Ok::<Bytes, Infallible>(Bytes::from(message))),
                    Err(_) => None,
                }
            });

            let initial = tokio_stream::iter(vec![Ok::<Bytes, Infallible>(Bytes::from(
                "event: connected\n\n",
            ))]);
            let body = Body::wrap_stream(initial.chain(event_stream));

            Ok(Response::builder()
                .status(StatusCode::OK)
                .header("content-type", "text/event-stream")
                .header("cache-control", "no-cache")
                .header("connection", "keep-alive")
                .body(body)
                .unwrap())
        }
        _ => Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .body(Body::from("Not found"))
            .unwrap()),
    }
}

/// Handle one JSON-RPC message. Returns `None` for notifications (no `id`,
/// no response expected).
fn handle_message(
    server_name: &str,
    tools: &HashMap<String, ToolEntry>,
    resources: &Vec<Value>,
    prompts: &Vec<Value>,
    event_sender: Option<&broadcast::Sender<String>>,
    line: &str,
) -> Option<Value> {
    let parsed: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            return Some(json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": { "code": -32700, "message": format!("Parse error: {e}") }
            }));
        }
    };

    let id = parsed.get("id").cloned();
    let method = parsed.get("method").and_then(Value::as_str).unwrap_or("");
    let params = parsed.get("params").cloned().unwrap_or(json!({}));

    // Requests without an "id" are notifications: no response is sent.
    let is_notification = id.is_none();

    let result = match method {
        "initialize" => Ok(json!({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": true,
                "prompts": true
            },
            "resources": resources,
            "prompts": prompts,
            "serverInfo": { "name": server_name, "version": env!("CARGO_PKG_VERSION") }
        })),
        "notifications/initialized" | "notifications/cancelled" => {
            return None; // nothing to do, no response
        }
        "tools/list" => {
            let tool_list: Vec<Value> = tools.values().map(|t| t.definition.clone()).collect();
            Ok(json!({ "tools": tool_list }))
        }
        "resources/list" => Ok(json!({ "resources": resources })),
        "prompts/list" => Ok(json!({ "prompts": prompts })),
        "resources/get" => get_resource(resources, &params),
        "prompts/get" => get_prompt(prompts, &params),
        "prompts/render" => render_prompt(prompts, &params),
        "tools/call" => handle_tool_call(tools, &params),
        other => Err((-32601, format!("Method not found: {other}"))),
    };

    if let Some(sender) = event_sender {
        if method == "tools/call" {
            let tool_name = params.get("name").and_then(Value::as_str).unwrap_or("unknown");
            let arguments = params.get("arguments").cloned().unwrap_or(json!({}));
            let status = match &result {
                Ok(_) => "ok",
                Err(_) => "error",
            };
            let payload = match &result {
                Ok(result) => json!({"status": status, "tool": tool_name, "arguments": arguments, "result": result}),
                Err((code, message)) => json!({"status": status, "tool": tool_name, "arguments": arguments, "error": {"code": code, "message": message}}),
            };
            let event_text = format!("event: tool_call\ndata: {}\n\n", payload.to_string());
            let _ = sender.send(event_text);
        }
    }

    if is_notification {
        return None;
    }

    Some(match result {
        Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
        Err((code, message)) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "error": { "code": code, "message": message }
        }),
    })
}
fn get_resource(resources: &Vec<Value>, params: &Value) -> Result<Value, (i64, String)> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or((-32602, "Missing or invalid resource name".into()))?;
    for resource in resources {
        if resource.get("name").and_then(Value::as_str) == Some(name) {
            return Ok(json!({ "resource": resource }));
        }
    }
    Err((-32601, format!("Resource not found: {name}")))
}

fn get_prompt(prompts: &Vec<Value>, params: &Value) -> Result<Value, (i64, String)> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or((-32602, "Missing or invalid prompt name".into()))?;
    for prompt in prompts {
        if prompt.get("name").and_then(Value::as_str) == Some(name) {
            return Ok(json!({ "prompt": prompt }));
        }
    }
    Err((-32601, format!("Prompt not found: {name}")))
}

fn render_prompt(prompts: &Vec<Value>, params: &Value) -> Result<Value, (i64, String)> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or((-32602, "Missing or invalid prompt name".into()))?;

    let variables = params.get("variables").cloned().unwrap_or(json!({}));
    let variables = variables
        .as_object()
        .ok_or((-32602, "Prompt variables must be an object".into()))?;

    let prompt = prompts
        .iter()
        .find(|prompt| prompt.get("name").and_then(Value::as_str) == Some(name))
        .ok_or((-32601, format!("Prompt not found: {name}")))?;

    let template = prompt
        .get("template")
        .and_then(Value::as_str)
        .ok_or((-32602, format!("Prompt '{}' has no template", name)))?;

    let rendered = render_template(template, variables);
    Ok(json!({ "rendered": rendered }))
}

fn render_template(template: &str, variables: &serde_json::Map<String, Value>) -> String {
    let mut result = template.to_string();
    for (key, value) in variables {
        let replacement = match value {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            Value::Bool(b) => b.to_string(),
            other => other.to_string(),
        };
        result = result.replace(&format!("{{{}}}", key), &replacement);
    }
    result
}

fn handle_tool_call(
    tools: &HashMap<String, ToolEntry>,
    params: &Value,
) -> Result<Value, (i64, String)> {
    let name = params
        .get("name")
        .and_then(Value::as_str)
        .ok_or((-32602, "Missing 'name' in tools/call params".to_string()))?;

    let arguments = params.get("arguments").cloned().unwrap_or(json!({}));

    let entry = tools
        .get(name)
        .ok_or((-32602, format!("Unknown tool: {name}")))?;

    // Rust-side schema validation, before Python is ever touched.
    if let Err(validation_error) = validate_arguments(entry, &arguments) {
        return Ok(json!({
            "content": [{ "type": "text", "text": format!("Invalid arguments: {validation_error}") }],
            "isError": true
        }));
    }

    match call_python_tool(&entry.func, &arguments) {
        Ok(text) => Ok(json!({
            "content": [{ "type": "text", "text": text }],
            "isError": false
        })),
        Err(err) => Ok(json!({
            "content": [{ "type": "text", "text": format!("Tool error: {err}") }],
            "isError": true
        })),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::prelude::*;
    use serde_json::json;
    use std::collections::HashMap;

    #[test]
    fn list_resources_and_prompts() {
        let tools = HashMap::<String, ToolEntry>::new();
        let resources = vec![json!({"name": "city_help", "description": "Help text", "content": "Use ISO codes."})];
        let prompts = vec![json!({"name": "weather_summary", "description": "Summary prompt", "template": "City: {city}"})];

        let request = json!({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}});
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();

        let returned_resources = response["result"]["resources"].as_array().expect("resources field must be an array");
        assert_eq!(returned_resources, &resources);

        let request = json!({"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}});
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();

        let returned_prompts = response["result"]["prompts"].as_array().expect("prompts field must be an array");
        assert_eq!(returned_prompts, &prompts);
    }

    #[test]
    fn tool_list_returns_definitions() {
        Python::with_gil(|py| {
            let mut tools = HashMap::new();
            let definition = json!({"name": "ping", "description": "Ping tool", "inputSchema": {"type": "object", "properties": {}}});
            let entry = ToolEntry {
                definition: definition.clone(),
                input_schema: json!({"type": "object", "properties": {}}),
                func: py.None().into(),
            };
            tools.insert("ping".to_string(), entry);

            let request = json!({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}});
            let response = handle_message(
                "weather",
                &tools,
                &Vec::new(),
                &Vec::new(),
                None,
                &request.to_string(),
            )
            .unwrap();

            assert_eq!(response["result"]["tools"][0], definition);
        });
    }

    #[test]
    fn get_resource_and_prompt_by_name() {
        let tools = HashMap::<String, ToolEntry>::new();
        let resources = vec![json!({"name": "city_help", "description": "Help text", "content": "Use ISO codes."})];
        let prompts = vec![json!({"name": "weather_summary", "description": "Summary prompt", "template": "City: {city}"})];

        let request = json!({"jsonrpc": "2.0", "id": 5, "method": "resources/get", "params": {"name": "city_help"}});
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();
        assert_eq!(response["result"]["resource"], resources[0]);

        let request = json!({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "weather_summary"}});
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();
        assert_eq!(response["result"]["prompt"], prompts[0]);
    }

    #[test]
    fn render_prompt_by_name() {
        let tools = HashMap::<String, ToolEntry>::new();
        let resources = Vec::new();
        let prompts = vec![json!({"name": "weather_summary", "description": "Summary prompt", "template": "City: {city}, Units: {units}"})];

        let request = json!({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "prompts/render",
            "params": {"name": "weather_summary", "variables": {"city": "Bengaluru", "units": "celsius"}}
        });
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();

        assert_eq!(response["result"]["rendered"], "City: Bengaluru, Units: celsius");
    }

    #[test]
    fn initialize_includes_resources_and_prompts() {
        let tools = HashMap::<String, ToolEntry>::new();
        let resources = vec![json!({"name": "city_help", "description": "Help text", "content": "Use ISO codes."})];
        let prompts = vec![json!({"name": "weather_summary", "description": "Summary prompt", "template": "City: {city}"})];

        let request = json!({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}});
        let response = handle_message(
            "weather",
            &tools,
            &resources,
            &prompts,
            None,
            &request.to_string(),
        )
        .unwrap();

        let returned_resources = response["result"]["resources"].as_array().expect("resources field must be an array");
        assert_eq!(returned_resources, &resources);
        let returned_prompts = response["result"]["prompts"].as_array().expect("prompts field must be an array");
        assert_eq!(returned_prompts, &prompts);
    }
}
