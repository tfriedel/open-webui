import asyncio
from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
import httpx
from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL


def create_insecure_httpx_client(headers=None, timeout=None, auth=None):
    """Create an httpx AsyncClient with SSL verification disabled.

    Note: verify=False must be passed at construction time because httpx
    configures the SSL context during __init__. Setting client.verify = False
    after construction does not affect the underlying transport's SSL context.
    """
    kwargs = {
        'follow_redirects': True,
        'verify': False,
    }
    if timeout is not None:
        kwargs['timeout'] = timeout
    if headers is not None:
        kwargs['headers'] = headers
    if auth is not None:
        kwargs['auth'] = auth
    return httpx.AsyncClient(**kwargs)


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = None

    async def connect(self, url: str, headers: Optional[dict] = None):
        exit_stack = AsyncExitStack()
        await exit_stack.__aenter__()
        try:
            if AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL:
                self._streams_context = streamablehttp_client(url, headers=headers)
            else:
                self._streams_context = streamablehttp_client(
                    url,
                    headers=headers,
                    httpx_client_factory=create_insecure_httpx_client,
                )

            transport = await exit_stack.enter_async_context(self._streams_context)
            read_stream, write_stream, _ = transport

            self._session_context = ClientSession(read_stream, write_stream)  # pylint: disable=W0201

            self.session = await exit_stack.enter_async_context(self._session_context)
            async with asyncio.timeout(30):
                await self.session.initialize()
            self.exit_stack = exit_stack
        except Exception as e:
            await asyncio.shield(exit_stack.aclose())
            raise e

    async def list_tool_specs(self) -> list[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.list_tools()
        tools = result.tools

        tool_specs = []
        for tool in tools:
            name = tool.name
            description = tool.description

            inputSchema = tool.inputSchema

            # TODO: handle outputSchema if needed
            outputSchema = getattr(tool, 'outputSchema', None)

            spec = {'name': name, 'description': description, 'parameters': inputSchema}

            # Preserve _meta (e.g. ui.resourceUri for MCP Apps)
            if tool.meta:
                spec['_meta'] = tool.meta

            tool_specs.append(spec)

        return tool_specs

    async def call_tool(self, function_name: str, function_args: dict) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.call_tool(function_name, function_args)
        if not result:
            raise Exception('No result returned from MCP tool call.')

        return result.model_dump(mode='json')

    async def list_resources(self, cursor: Optional[str] = None) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.list_resources(cursor=cursor)
        if not result:
            raise Exception('No result returned from MCP list_resources call.')

        result_dict = result.model_dump()
        resources = result_dict.get('resources', [])

        return resources

    async def read_resource(self, uri: str) -> Optional[dict]:
        if not self.session:
            raise RuntimeError('MCP client is not connected.')

        result = await self.session.read_resource(uri)
        if not result:
            raise Exception('No result returned from MCP read_resource call.')
        result_dict = result.model_dump()

        return result_dict

    async def disconnect(self):
        # Clean up and close the session
        if self.exit_stack:
            await self.exit_stack.aclose()

    async def __aenter__(self):
        await self.exit_stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.exit_stack.__aexit__(exc_type, exc_value, traceback)
        await self.disconnect()
