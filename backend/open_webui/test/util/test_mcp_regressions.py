import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from open_webui.routers.mcp import _get_system_oauth_access_token
from open_webui.utils.middleware import (
    has_function_call_output,
    normalize_mcp_tool_result,
)


def test_normalize_mcp_tool_result_preserves_structured_content():
    result = {
        'content': [{'type': 'text', 'text': 'ok'}],
        'structuredContent': {'answer': 42},
        'isError': False,
    }

    assert normalize_mcp_tool_result(result) == result


def test_has_function_call_output_scans_entire_output():
    output = [
        {
            'type': 'message',
            'content': [{'type': 'output_text', 'text': 'hello'}],
        },
        {
            'type': 'function_call',
            'call_id': 'call_1',
            'name': 'server_tool',
        },
    ]

    assert has_function_call_output(output) is True


def test_get_system_oauth_access_token_reads_session_token_when_header_missing():
    oauth_manager = SimpleNamespace(
        get_oauth_token=AsyncMock(return_value={'access_token': 'system-token'})
    )
    request = SimpleNamespace(
        headers={},
        cookies={'oauth_session_id': 'oauth-session'},
        app=SimpleNamespace(state=SimpleNamespace(oauth_manager=oauth_manager)),
    )
    user = SimpleNamespace(id='user-1')

    token = asyncio.run(_get_system_oauth_access_token(request, user))

    assert token == 'system-token'
    oauth_manager.get_oauth_token.assert_awaited_once_with('user-1', 'oauth-session')
