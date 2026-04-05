"""Pydantic models for MCP Apps API responses."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class McpUiResourceCsp(BaseModel):
    """CSP configuration from resource metadata per MCP ext-apps spec."""

    connectDomains: Optional[List[str]] = None
    resourceDomains: Optional[List[str]] = None
    frameDomains: Optional[List[str]] = None
    baseUriDomains: Optional[List[str]] = None


class MCPUIPermissions(BaseModel):
    """Permissions for iframe capabilities."""

    camera: Optional[Dict[str, Any]] = None
    microphone: Optional[Dict[str, Any]] = None
    geolocation: Optional[Dict[str, Any]] = None
    clipboardWrite: Optional[Dict[str, Any]] = None


class MCPAppResource(BaseModel):
    """UI resource fetched from an MCP server."""

    uri: str
    content: str
    mimeType: str = "text/html"
    csp: Optional[McpUiResourceCsp] = None
    permissions: Optional[MCPUIPermissions] = None


class MCPToolResult(BaseModel):
    """Response from a tool call."""

    content: List[Dict[str, Any]]
    structuredContent: Optional[Any] = None
    isError: bool = False
