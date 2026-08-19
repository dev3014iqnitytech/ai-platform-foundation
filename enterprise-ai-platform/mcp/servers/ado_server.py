"""
Azure DevOps MCP Server — Implements the MCP HTTP transport for all ADO operations.

Exposes POST /tools/{tool_name} endpoints consumed by MCPClient.
Owns all ADO auth (PAT token) — agents never hold credentials directly.

Tools exposed:
  get_work_item        — fetch a work item with selected fields + relations
  create_test_plan     — create a Test Plan linked to a User Story
  create_test_suite    — create a static Test Suite inside a plan
  create_test_case     — create a Test Case work item (JSON Patch)
  add_test_to_suite    — assign a test case to a suite
  link_work_items      — create a relation between two work items

Run:  uvicorn mcp.servers.ado_server:app --port 8001
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import fields
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from structlog import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="ADO MCP Server",
    version="1.0.0",
    default_response_class=ORJSONResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared ADO client — one connection pool for the server process lifetime
# ─────────────────────────────────────────────────────────────────────────────

_ado_client: httpx.AsyncClient | None = None


def _get_settings():
    import sys, pathlib
    # backend/ must be on sys.path for `app` package to resolve
    _backend = str(pathlib.Path(__file__).parents[2] / "backend")
    if _backend not in sys.path:
        sys.path.insert(0, _backend)
    from app.core.config import settings
    return settings


def _auth_header() -> str:
    pat = _get_settings().ADO_PAT.get_secret_value()
    return f"Bearer {pat}"


async def _ado() -> httpx.AsyncClient:
    global _ado_client
    if _ado_client is None:
        s = _get_settings()
        _ado_client = httpx.AsyncClient(
            base_url=str(s.ADO_ORGANIZATION),
            headers={
                "Authorization": _auth_header(),
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _ado_client


# ─────────────────────────────────────────────────────────────────────────────
# MCP envelope models
# ─────────────────────────────────────────────────────────────────────────────

class ToolRequest(BaseModel):
    arguments: dict[str, Any]


class ToolResponse(BaseModel):
    result: dict[str, Any] | None = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """MCPClient pings this before marking the server healthy."""
    try:
        s = _get_settings()
        client = await _ado()
        # Lightweight call — just verifies auth and connectivity
        resp = await client.get(
            f"/{s.ADO_PROJECT}/_apis/projects?api-version=7.1&$top=1"
        )
        return {"status": "ok", "ado_reachable": resp.status_code == 200}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/tools/{tool_name}", response_model=ToolResponse)
async def call_tool(tool_name: str, body: ToolRequest):
    handlers = {
        "get_work_item":    _tool_get_work_item,
        "create_test_plan": _tool_create_test_plan,
        "create_test_suite":_tool_create_test_suite,
        "create_test_case": _tool_create_test_case,
        "add_test_to_suite":_tool_add_test_to_suite,
        "link_work_items":  _tool_link_work_items,
    }
    handler = handlers.get(tool_name)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        result = await handler(body.arguments)
        return ToolResponse(result=result)
    except httpx.HTTPStatusError as e:
        logger.error("ado_tool_http_error", tool=tool_name, status=e.response.status_code)
        return ToolResponse(error=f"ADO HTTP {e.response.status_code}: {e.response.text[:300]}")
    except Exception as e:
        logger.error("ado_tool_error", tool=tool_name, error=str(e), exc_info=True)
        return ToolResponse(error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _tool_get_work_item(args: dict) -> dict:
    """
    args: {id}
    returns: raw ADO work item JSON
    """
    s = _get_settings()
    item_id = args["id"]
    fields = ",".join(args.get("fields", []))
    # expand = args.get("expand", "relations")

    client = await _ado()
    base = f"{s.ADO_ORGANIZATION}/{s.ADO_PROJECT}/_apis/wit/workitems/{item_id}"
    api_ver = 7.1

    # Call 1: fields
    r1 = await client.get(f"{base}?fields={fields}&api-version={api_ver}")
    
    if r1.status_code == 404:
        raise ValueError(f"Work item {item_id} not found")
    r1.raise_for_status()
    data = r1.json()

    # Call 2: relations (separate request — ADO rejects fields+expand together)
    r2 = await client.get(f"{base}?$expand=relations&api-version={api_ver}")
    data["relations"] = r2.json().get("relations", []) if r2.status_code == 200 else []
    return data


async def _tool_create_test_plan(args: dict) -> dict:
    """
    args: {name, description, story_id}
    returns: {id, name, url}
    """
    s = _get_settings()
    client = await _ado()
    resp = await client.post(
        f"/{s.ADO_PROJECT}/_apis/test/plans?api-version={s.ADO_API_VERSION}",
        json={
            "name": args["name"],
            "description": args.get("description", ""),
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "name": data["name"], "url": data.get("url", "")}


async def _tool_create_test_suite(args: dict) -> dict:
    """
    args: {plan_id, name, suite_type}
    returns: {id, name}
    """
    s = _get_settings()
    client = await _ado()
    resp = await client.post(
        f"/{s.ADO_PROJECT}/_apis/test/plans/{args['plan_id']}/suites"
        f"?api-version={s.ADO_API_VERSION}",
        json={
            "name": args["name"],
            "suiteType": args.get("suite_type", "staticTestSuite"),
        },
    )
    resp.raise_for_status()
    data = resp.json()
    suite = data.get("value", [data])[0] if isinstance(data.get("value"), list) else data
    return {"id": suite["id"], "name": suite["name"]}


async def _tool_create_test_case(args: dict) -> dict:
    """
    args: {title, steps, priority, tags, description}
    returns: {id, url}
    """
    s = _get_settings()
    client = await _ado()

    steps_xml = _build_steps_xml(args.get("steps", []))
    patch = [
        {"op": "add", "path": "/fields/System.Title",
         "value": args["title"]},
        {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.Steps",
         "value": steps_xml},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority",
         "value": str(args.get("priority", 2))},
        {"op": "add", "path": "/fields/System.Tags",
         "value": "; ".join(args.get("tags", []))},
        {"op": "add", "path": "/fields/System.Description",
         "value": args.get("description", "")},
    ]
    resp = await client.post(
        f"/{s.ADO_PROJECT}/_apis/wit/workitems/$Test%20Case"
        f"?api-version={s.ADO_API_VERSION}",
        content=__import__("json").dumps(patch).encode(),
        headers={"Content-Type": "application/json-patch+json"},
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "url": data.get("url", "")}


async def _tool_add_test_to_suite(args: dict) -> dict:
    """
    args: {plan_id, suite_id, test_case_id}
    returns: {ok: true}
    """
    s = _get_settings()
    client = await _ado()
    resp = await client.post(
        f"/{s.ADO_PROJECT}/_apis/test/plans/{args['plan_id']}"
        f"/suites/{args['suite_id']}/testcases"
        f"?api-version={s.ADO_API_VERSION}",
        json=[{"id": args["test_case_id"]}],
    )
    resp.raise_for_status()
    return {"ok": True}


async def _tool_link_work_items(args: dict) -> dict:
    """
    args: {source_id, target_id, relation_type}
    relation_type default: "Microsoft.VSTS.Common.TestedBy-Reverse"
    returns: {ok: true}
    """
    s = _get_settings()
    client = await _ado()
    target_url = (
        f"{s.ADO_ORGANIZATION}/_apis/wit/workitems/{args['target_id']}"
    )
    patch = [{
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": args.get("relation_type", "Microsoft.VSTS.Common.TestedBy-Reverse"),
            "url": target_url,
        },
    }]
    resp = await client.patch(
        f"/_apis/wit/workitems/{args['source_id']}"
        f"?api-version={s.ADO_API_VERSION}",
        content=__import__("json").dumps(patch).encode(),
        headers={"Content-Type": "application/json-patch+json"},
    )
    resp.raise_for_status()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_steps_xml(steps: list[dict]) -> str:
    rows = []
    for i, step in enumerate(steps, start=1):
        action = step.get("action", step.get("step", ""))
        expected = step.get("expected_result", step.get("expected", ""))
        rows.append(
            f'<step id="{i}" type="ActionStep">'
            f'<parameterizedString isformatted="true">{action}</parameterizedString>'
            f'<parameterizedString isformatted="true">{expected}</parameterizedString>'
            f"</step>"
        )
    return f'<steps id="0" last="{len(rows)}">{"".join(rows)}</steps>'
