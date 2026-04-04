"""
MCP Apps Router.

Provides endpoints for reading UI resources and calling tools on MCP servers.
Used by the frontend to render MCP App UIs and relay tool calls from
sandboxed iframes.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.utils.auth import get_verified_user
from open_webui.models.users import UserModel
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.mcp.models import MCPAppResource, MCPToolResult
from open_webui.utils.access_control import has_connection_access

log = logging.getLogger(__name__)

router = APIRouter()


def _get_resource_uri(spec: dict) -> str | None:
    """Extract the ui:// resource URI from a tool spec's _meta, if present."""
    meta = spec.get("_meta", {})
    ui = meta.get("ui", {}) if meta else {}
    return ui.get("resourceUri") or (meta.get("ui/resourceUri") if meta else None)


class ResolveAppRequest(BaseModel):
    tool_name: str


class ResolveAppResponse(BaseModel):
    resourceUri: str
    serverId: str


class ReadResourceRequest(BaseModel):
    server_id: str
    uri: str


class ReadResourceResponse(BaseModel):
    resource: MCPAppResource


class CallToolRequest(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict = {}


async def _get_mcp_client(
    request: Request, server_id: str, user: UserModel
) -> MCPClient:
    """Create a connected MCP client for the given server, with access checks."""
    tool_servers = request.app.state.config.TOOL_SERVER_CONNECTIONS
    server_connection = None

    for server in tool_servers:
        if (
            server.get("type", "") == "mcp"
            and server.get("info", {}).get("id", "") == server_id
        ):
            server_connection = server
            break

    if not server_connection:
        raise HTTPException(status_code=404, detail=f"MCP server '{server_id}' not found")

    if not has_connection_access(user, server_connection):
        raise HTTPException(status_code=403, detail=f"Access denied to MCP server '{server_id}'")

    # Build auth headers — mirror the logic in middleware.py
    headers = {}
    auth_type = server_connection.get("auth_type", "")
    if auth_type == "bearer":
        headers["Authorization"] = f"Bearer {server_connection.get('key', '')}"
    elif auth_type == "session":
        headers["Authorization"] = f"Bearer {request.state.token.credentials}"
    elif auth_type == "system_oauth":
        oauth_token = request.headers.get("x-oauth-access-token", "")
        if oauth_token:
            headers["Authorization"] = f"Bearer {oauth_token}"
    elif auth_type in ("oauth_2.1", "oauth_2.1_static"):
        try:
            oauth_token = await request.app.state.oauth_client_manager.get_oauth_token(
                user.id, f"mcp:{server_id}"
            )
            if oauth_token:
                headers["Authorization"] = f"Bearer {oauth_token.get('access_token', '')}"
        except Exception as e:
            log.error(f"Error getting OAuth token for MCP server '{server_id}': {e}")

    connection_headers = server_connection.get("headers", None)
    if connection_headers:
        if isinstance(connection_headers, list):
            for header in connection_headers:
                headers[header.get("key", "")] = header.get("value", "")
        elif isinstance(connection_headers, dict):
            headers.update(connection_headers)

    client = MCPClient()
    try:
        await client.connect(
            url=server_connection.get("url", ""),
            headers=headers if headers else None,
        )
    except Exception as e:
        log.error(f"Failed to connect to MCP server '{server_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect to MCP server: {str(e)}")

    return client


@router.post("/resolve-app", response_model=ResolveAppResponse)
async def resolve_mcp_app(
    request: Request,
    body: ResolveAppRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Check if a tool has an MCP App UI. Returns resourceUri + serverId or 404."""
    tool_name = body.tool_name
    tool_servers = request.app.state.config.TOOL_SERVER_CONNECTIONS

    # Match tool name prefix against known MCP server IDs using longest-match-wins
    # to avoid ambiguity when server IDs share prefixes (e.g. "foo" vs "foo_bar").
    server_id = None
    for server in tool_servers:
        if server.get("type", "") != "mcp":
            continue
        sid = server.get("info", {}).get("id", "")
        if sid and tool_name.startswith(f"{sid}_"):
            if server_id is None or len(sid) > len(server_id):
                server_id = sid

    if not server_id:
        raise HTTPException(status_code=404, detail="Not an MCP tool")

    actual_tool_name = tool_name[len(f"{server_id}_"):]
    client = await _get_mcp_client(request, server_id, user)

    try:
        tool_specs = await client.list_tool_specs() or []
        for spec in tool_specs:
            if spec.get("name") == actual_tool_name:
                uri = _get_resource_uri(spec)
                if uri and uri.startswith("ui://"):
                    return ResolveAppResponse(resourceUri=uri, serverId=server_id)
                raise HTTPException(status_code=404, detail="Tool has no MCP App UI")
        raise HTTPException(status_code=404, detail=f"Tool '{actual_tool_name}' not found")
    finally:
        await client.disconnect()


@router.post("/resource", response_model=ReadResourceResponse)
async def read_resource(
    request: Request,
    body: ReadResourceRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Read a UI resource from an MCP server."""
    if not body.uri.startswith("ui://"):
        raise HTTPException(status_code=400, detail="Invalid resource URI. Must start with 'ui://'")

    client = await _get_mcp_client(request, body.server_id, user)

    try:
        # Verify the requested URI is actually advertised by a tool on this server
        tool_specs = await client.list_tool_specs() or []
        advertised = False
        for spec in tool_specs:
            if _get_resource_uri(spec) == body.uri:
                advertised = True
                break
        if not advertised:
            raise HTTPException(status_code=403, detail="Resource URI not advertised by any tool on this server")

        result = await client.read_resource(body.uri)
        if not result:
            raise HTTPException(status_code=404, detail="Resource not found")

        content = ""
        mime_type = "text/html"
        for item in result.get("contents", []):
            if item.get("text"):
                content = item.get("text", "")
            if item.get("mimeType"):
                mime_type = item.get("mimeType")

        return ReadResourceResponse(
            resource=MCPAppResource(uri=body.uri, mimeType=mime_type, content=content)
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to read resource '{body.uri}': {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read resource: {str(e)}")
    finally:
        await client.disconnect()


@router.post("/tool/call")
async def call_tool(
    request: Request,
    body: CallToolRequest,
    user: UserModel = Depends(get_verified_user),
):
    """Call a tool on an MCP server (relayed from an app iframe)."""
    client = await _get_mcp_client(request, body.server_id, user)

    try:
        # Verify the tool exists on this server before calling
        tool_specs = await client.list_tool_specs() or []
        tool_names = {spec.get("name") for spec in tool_specs}
        if body.tool_name not in tool_names:
            raise HTTPException(status_code=404, detail=f"Tool '{body.tool_name}' not found on server '{body.server_id}'")

        result = await client.call_tool(body.tool_name, body.arguments)

        if result is None:
            return MCPToolResult(content=[{"type": "text", "text": ""}], isError=False)
        if isinstance(result, list):
            return MCPToolResult(content=result, isError=False)
        return MCPToolResult(content=[{"type": "text", "text": str(result)}], isError=False)

    except Exception as e:
        log.error(f"Tool call failed for '{body.tool_name}': {e}")
        return MCPToolResult(content=[{"type": "text", "text": str(e)}], isError=True)
    finally:
        await client.disconnect()
