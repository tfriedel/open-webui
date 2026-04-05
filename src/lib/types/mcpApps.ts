/**
 * MCP Apps types for model context tracking.
 */

export interface MCPAppResource {
	uri: string;
	content: string;
	mimeType: string;
}

export type MCPAppDisplayMode = 'inline' | 'fullscreen' | 'pip';
export type MCPAppState = 'loading' | 'initializing' | 'ready' | 'error' | 'closed';

export interface MCPAppInstance {
	instanceId: string;
	serverId: string;
	toolName: string;
	resource: MCPAppResource;
	state: MCPAppState;
	displayMode: MCPAppDisplayMode;
	height: number;
	title: string;
	error?: string;
	toolCallId?: string | number;
	modelContext?: string;
	createdAt: number;
}
