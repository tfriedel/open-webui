/**
 * MCP Apps State Store.
 *
 * Tracks active MCP App instances for model context updates.
 * ResponseMessage watches this store to persist app context
 * into chat messages so the LLM can access live app state.
 */

import { writable, type Writable } from 'svelte/store';
import type { MCPAppInstance, MCPAppResource } from '$lib/types/mcpApps';

export const mcpApps: Writable<Map<string, MCPAppInstance>> = writable(new Map());

export function generateInstanceId(): string {
	return `mcp-app-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

export function createAppInstance(
	params: Pick<MCPAppInstance, 'serverId' | 'toolName' | 'resource' | 'toolCallId'>
): MCPAppInstance {
	return {
		instanceId: generateInstanceId(),
		serverId: params.serverId,
		toolName: params.toolName,
		resource: params.resource,
		state: 'loading',
		displayMode: 'inline',
		height: 400,
		title: params.toolName,
		toolCallId: params.toolCallId,
		createdAt: Date.now()
	};
}

export function addApp(app: MCPAppInstance): void {
	mcpApps.update((apps) => {
		apps.set(app.instanceId, app);
		return apps;
	});
}

export function updateApp(instanceId: string, updates: Partial<MCPAppInstance>): void {
	mcpApps.update((apps) => {
		const app = apps.get(instanceId);
		if (app) {
			apps.set(instanceId, { ...app, ...updates });
		}
		return apps;
	});
}

export function updateAppModelContext(instanceId: string, modelContext: string): void {
	updateApp(instanceId, { modelContext });
}

export function removeApp(instanceId: string): void {
	mcpApps.update((apps) => {
		apps.delete(instanceId);
		return apps;
	});
}
