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
use tokio::sync::Mutex as AsyncMutex;

const PROTOCOL_VERSION: &str = "2024-11-05";

pub async fn serve_stdio(
    server_name: String,
    tools: Arc<HashMap<String, ToolEntry>>,
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
        let stdout = Arc::clone(&stdout);
        let server_name = server_name.clone();

        tokio::spawn(async move {
            if let Some(response) = handle_message(&server_name, &tools, &line) {
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
    address: String,
) -> Result<(), String> {
    let make_svc = make_service_fn(move |_conn| {
        let tools = Arc::clone(&tools);
        let server_name = server_name.clone();
        async move {
            Ok::<_, Infallible>(service_fn(move |req| {
                handle_http_request(req, server_name.clone(), Arc::clone(&tools))
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
            let response = match handle_message(&server_name, &tools, line) {
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
            let body = Body::from("event: connected\n\n");
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
fn handle_message(server_name: &str, tools: &HashMap<String, ToolEntry>, line: &str) -> Option<Value> {
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
            "capabilities": { "tools": {} },
            "serverInfo": { "name": server_name, "version": env!("CARGO_PKG_VERSION") }
        })),
        "notifications/initialized" | "notifications/cancelled" => {
            return None; // nothing to do, no response
        }
        "tools/list" => {
            let tool_list: Vec<Value> = tools.values().map(|t| t.definition.clone()).collect();
            Ok(json!({ "tools": tool_list }))
        }
        "tools/call" => handle_tool_call(tools, &params),
        other => Err((-32601, format!("Method not found: {other}"))),
    };

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
