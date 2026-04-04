"""
Regression tests for MCP Apps integration.

These tests verify the critical data flows that enable MCP Apps to work:
- Server connection lifecycle (connect → list tools → call tool → disconnect)
- Tool result propagation (MCP server → middleware → frontend → app SDK)
- State persistence (save via tools/call relay → load on refresh)

Each test documents which commit introduced the regression it guards against.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.routers.mcp import _get_system_oauth_access_token
from open_webui.utils.middleware import (
    has_function_call_output,
    normalize_mcp_tool_result,
)


# ---------------------------------------------------------------------------
# 1. MCP client can connect, list tools, and call tools without crashing
#
# Regression: anyio.fail_after cancel scope conflicted with the transport's
# internal task group, causing ExceptionGroup on every connection attempt.
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine, skipping if mcp-demo is unreachable."""
    try:
        return asyncio.run(coro)
    except Exception as e:
        if 'connect' in str(type(e).__name__).lower() or 'timeout' in str(e).lower():
            pytest.skip('mcp-demo server not reachable')
        raise


def test_mcp_client_connect_and_list_tools():
    """MCPClient must connect, list tools, and disconnect cleanly.

    Requires the mcp-demo container (docker-compose.test.yaml).
    Skips if the server is unreachable.
    """
    async def _test():
        from open_webui.utils.mcp.client import MCPClient

        client = MCPClient()
        await client.connect('http://mcp-demo:8000/mcp')
        try:
            specs = await client.list_tool_specs()
            assert isinstance(specs, list)
            assert len(specs) > 0
            assert all('name' in s for s in specs)
        finally:
            await client.disconnect()

    _run_async(_test())


def test_mcp_client_call_tool_returns_content_and_structured():
    """call_tool must return a dict with 'content' list and optionally
    'structuredContent', matching the MCP CallToolResult schema.
    """
    async def _test():
        from open_webui.utils.mcp.client import MCPClient

        client = MCPClient()
        await client.connect('http://mcp-demo:8000/mcp')
        try:
            result = await client.call_tool('open_converter', {
                'category': 'length',
                'value': 1.0,
                'from_unit': 'm',
                'to_unit': 'cm',
            })

            assert isinstance(result, dict)
            assert 'content' in result
            assert isinstance(result['content'], list)
            assert any(item.get('type') == 'text' for item in result['content'])
        finally:
            await client.disconnect()

    _run_async(_test())


# ---------------------------------------------------------------------------
# 2. normalize_mcp_tool_result extracts the content list
#
# Regression (c6bf86338): returned the full MCP envelope dict instead of the
# content array. This broke process_tool_result which checks isinstance(list)
# to enter the MCP content extraction branch. Without that, the tool result
# was str()-serialized as a Python repr, losing the structured state_id the
# app needs for save/load.
# ---------------------------------------------------------------------------

def test_normalize_extracts_content_list():
    """Must return the content array so process_tool_result can iterate it."""
    result = {
        'content': [
            {'type': 'text', 'text': 'Unit Converter opened.'},
            {'type': 'text', 'text': '{"state_id": "abc-123"}'},
        ],
        'structuredContent': {'state_id': 'abc-123'},
        'isError': False,
        'meta': None,
    }

    normalized = normalize_mcp_tool_result(result)

    assert isinstance(normalized, list), (
        'Must be a list so process_tool_result enters the MCP branch'
    )
    assert normalized == result['content']


def test_normalize_raises_on_error():
    result = {'content': 'something went wrong', 'isError': True}
    with pytest.raises(Exception, match='something went wrong'):
        normalize_mcp_tool_result(result)


def test_normalize_passes_through_non_dict():
    assert normalize_mcp_tool_result('plain string') == 'plain string'
    assert normalize_mcp_tool_result(None) is None


def test_normalize_falls_back_to_full_dict_when_no_content_key():
    result = {'custom_key': 'value'}
    assert normalize_mcp_tool_result(result) == result


# ---------------------------------------------------------------------------
# 3. Tool result round-trip: MCP content → middleware → attributes.result
#    preserves state_id so the app can save/load state
#
# Regression (c6bf86338 + 8dc6019e0): the middleware returned the full
# envelope dict, which process_tool_result couldn't parse. The state_id
# was lost, so the app couldn't save state or load it on refresh.
# ---------------------------------------------------------------------------

def test_mcp_tool_result_round_trip_preserves_state_id():
    """Simulate the full tool result pipeline and verify state_id survives.

    Flow: MCPClient.call_tool() → normalize_mcp_tool_result() →
    process_tool_result() → serialize_output() → attributes.result →
    frontend parseJSONString() → toolResult prop → tool-result notification.
    """
    import html as html_mod

    # Step 1: What MCPClient.call_tool returns (MCP CallToolResult dict)
    mcp_result = {
        'content': [
            {'type': 'text', 'text': 'Unit Converter opened.'},
            {'type': 'text', 'text': json.dumps({'state_id': 'test-uuid-123', 'category': 'length'})},
        ],
        'structuredContent': {'state_id': 'test-uuid-123', 'category': 'length'},
        'isError': False,
        'meta': None,
    }

    # Step 2: normalize_mcp_tool_result (inside the MCP tool function wrapper)
    normalized = normalize_mcp_tool_result(mcp_result)
    assert isinstance(normalized, list), 'normalize must return content list'

    # Step 3: process_tool_result MCP branch extracts text items
    # (simplified — just the text extraction logic from lines 1076-1108)
    tool_response = []
    for item in normalized:
        if isinstance(item, dict) and item.get('type') == 'text':
            text = item.get('text', '')
            if isinstance(text, str):
                try:
                    text = json.loads(text)
                except json.JSONDecodeError:
                    pass
            tool_response.append(text)
    tool_result = tool_response[0] if len(tool_response) == 1 else tool_response

    # Step 4: process_tool_result wraps lists and JSON-encodes
    if isinstance(tool_result, list):
        tool_result = {'results': tool_result}
    if isinstance(tool_result, dict):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

    # Step 5: serialize_output builds result_text (same as tool_result here)
    result_text = tool_result

    # Step 6: Encode into <details> tag attribute
    encoded = html_mod.escape(json.dumps(result_text, ensure_ascii=False))

    # Step 7: Frontend decodes (html entities → JSON parse → recursive parse)
    decoded = html_mod.unescape(encoded)
    # parseJSONString recursively: JSON.parse until non-string
    parsed = json.loads(decoded)  # unwrap outer JSON encoding
    if isinstance(parsed, str):
        parsed = json.loads(parsed)  # unwrap inner JSON string

    # Step 8: Verify state_id is recoverable
    assert isinstance(parsed, dict)
    results = parsed.get('results', [])
    state_ids = []
    for r in results:
        if isinstance(r, dict) and 'state_id' in r:
            state_ids.append(r['state_id'])

    assert 'test-uuid-123' in state_ids, (
        'state_id must survive the full round-trip so the app can save/load state'
    )


# ---------------------------------------------------------------------------
# 4. State persistence: save → get_results returns saved data
#
# Regression: without state_id reaching the app, save_converter_state was
# never called, so get_converter_results always returned empty.
# This test verifies the server-side contract directly.
# ---------------------------------------------------------------------------

def test_save_and_retrieve_converter_state():
    """Save state via save_converter_state, then verify get_converter_results
    returns it. This is the server-side half of the state persistence flow.
    """
    async def _test():
        from open_webui.utils.mcp.client import MCPClient

        client = MCPClient()
        await client.connect('http://mcp-demo:8000/mcp')
        try:
            # Open converter to get a state_id
            open_result = await client.call_tool('open_converter', {
                'category': 'length',
                'value': 2.54,
                'from_unit': 'cm',
                'to_unit': 'in',
            })
            # Extract state_id from structured content or text
            state_id = None
            if open_result.get('structuredContent'):
                state_id = open_result['structuredContent'].get('state_id')
            if not state_id:
                for item in open_result.get('content', []):
                    if item.get('type') == 'text':
                        try:
                            data = json.loads(item['text'])
                            if isinstance(data, dict) and 'state_id' in data:
                                state_id = data['state_id']
                        except (json.JSONDecodeError, KeyError):
                            pass
            assert state_id, 'open_converter must return a state_id'

            # Save state (simulating what the app does via tools/call relay)
            save_result = await client.call_tool('save_converter_state', {
                'state_id': state_id,
                'state': {
                    'category': 'length',
                    'fromUnit': 'cm',
                    'toUnit': 'in',
                    'fromValue': 2.54,
                    'history': [{'fromValue': 2.54, 'fromUnit': 'cm', 'toValue': 1.0, 'toUnit': 'in'}],
                },
            })
            assert not save_result.get('isError'), 'save_converter_state must succeed'

            # Retrieve results (what the LLM calls when user asks about results)
            get_result = await client.call_tool('get_converter_results', {})
            result_text = ''
            for item in get_result.get('content', []):
                if item.get('type') == 'text':
                    result_text += item['text']

            assert 'No converter session active' not in result_text, (
                'get_converter_results must return saved state, not "no session"'
            )
            assert '2.54' in result_text, 'Result must contain the saved conversion value'
            assert 'cm' in result_text, 'Result must contain the saved unit'
        finally:
            await client.disconnect()

    _run_async(_test())


# ---------------------------------------------------------------------------
# 5. Utility function tests
# ---------------------------------------------------------------------------

def test_has_function_call_output_scans_entire_output():
    output = [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'hello'}]},
        {'type': 'function_call', 'call_id': 'call_1', 'name': 'server_tool'},
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
