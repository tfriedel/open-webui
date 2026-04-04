/**
 * MCP Apps API Client.
 *
 * Fetches UI resources from MCP servers for rendering in iframes.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface MCPAppResource {
	uri: string;
	content: string;
	mimeType: string;
	csp?: {
		connectDomains?: string[];
		resourceDomains?: string[];
		frameDomains?: string[];
		baseUriDomains?: string[];
	};
	permissions?: {
		camera?: Record<string, unknown>;
		microphone?: Record<string, unknown>;
		geolocation?: Record<string, unknown>;
		clipboardWrite?: Record<string, unknown>;
	};
}

export interface MCPResolvedApp {
	resourceUri: string;
	serverId: string;
}

export const resolveMcpApp = async (
	token: string,
	toolName: string
): Promise<MCPResolvedApp | null> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/mcp/resolve-app`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ tool_name: toolName })
	});

	if (!res.ok) return null;
	return res.json();
};

export const readResource = async (
	token: string,
	serverId: string,
	uri: string
): Promise<MCPAppResource> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/mcp/resource`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			server_id: serverId,
			uri: uri
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail || err.message || 'Failed to fetch resource';
			return null;
		});

	if (error) {
		throw error;
	}

	return res.resource;
};
