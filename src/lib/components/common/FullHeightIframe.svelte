<script lang="ts">
	import { createEventDispatcher, onDestroy, onMount, tick } from 'svelte';

	const dispatch = createEventDispatcher();

	// Props
	export let src: string | null = null; // URL or raw HTML (auto-detected)
	export let title = 'Embedded Content';
	export let initialHeight: number | null = null; // initial height in px, null = auto

	export let iframeClassName = 'w-full rounded-2xl';

	export let args = null;

	export let allowScripts = true;
	export let allowForms = false;

	export let allowSameOrigin = false; // set to true only when you trust the content
	export let allowPopups = false;
	export let allowDownloads = true;

	export let referrerPolicy: HTMLIFrameElement['referrerPolicy'] =
		'strict-origin-when-cross-origin';
	export let allowFullscreen = true;

	export let payload = null; // payload to send into the iframe on request
	export let isMcpApp = false; // true only when this iframe hosts an MCP App
	export let serverId: string | null = null; // MCP server ID for relaying tool calls
	export let toolResult: string | null = null; // MCP tool result text to send after tool-input

	let iframe: HTMLIFrameElement | null = null;
	let iframeSrc: string | null = null;
	let iframeDoc: string | null = null;

	// Derived: build sandbox attribute from flags
	$: sandbox =
		[
			allowScripts && 'allow-scripts',
			allowForms && 'allow-forms',
			allowSameOrigin && 'allow-same-origin',
			allowPopups && 'allow-popups',
			allowDownloads && 'allow-downloads'
		]
			.filter(Boolean)
			.join(' ') || undefined;

	// Detect URL vs raw HTML and prep src/srcdoc
	$: isUrl = typeof src === 'string' && /^(https?:)?\/\//i.test(src);
	$: if (src) {
		mcpInitDone = false;
		setIframeSrc();
	}

	const setIframeSrc = async () => {
		await tick();
		if (isUrl) {
			iframeSrc = src as string;
			iframeDoc = null;
		} else {
			iframeDoc = await processHtmlForDeps(src as string);
			iframeSrc = null;
		}
	};

	// Alpine directives detection
	const alpineDirectives = [
		'x-data',
		'x-init',
		'x-show',
		'x-bind',
		'x-on',
		'x-text',
		'x-html',
		'x-model',
		'x-modelable',
		'x-ref',
		'x-for',
		'x-if',
		'x-effect',
		'x-transition',
		'x-cloak',
		'x-ignore',
		'x-teleport',
		'x-id'
	];

	async function processHtmlForDeps(html: string): Promise<string> {
		if (!allowSameOrigin) return html;

		const scriptTags: string[] = [];

		// --- Alpine.js detection & injection ---
		const hasAlpineDirectives = alpineDirectives.some((dir) => html.includes(dir));
		if (hasAlpineDirectives) {
			try {
				const { default: alpineCode } = await import('alpinejs/dist/cdn.min.js?raw');
				const alpineBlob = new Blob([alpineCode], { type: 'text/javascript' });
				const alpineUrl = URL.createObjectURL(alpineBlob);
				const alpineTag = `<script src="${alpineUrl}" defer><\/script>`;
				scriptTags.push(alpineTag);
			} catch (error) {
				console.error('Error processing Alpine for iframe:', error);
			}
		}

		// --- Chart.js detection & injection ---
		const chartJsDirectives = ['new Chart(', 'Chart.'];
		const hasChartJsDirectives = chartJsDirectives.some((dir) => html.includes(dir));
		if (hasChartJsDirectives) {
			try {
				// import chartUrl from 'chart.js/auto?url';
				const { default: Chart } = await import('chart.js/auto');
				(window as any).Chart = Chart;

				const chartTag = `<script>
window.Chart = parent.Chart; // Chart previously assigned on parent
<\/script>`;
				scriptTags.push(chartTag);
			} catch (error) {
				console.error('Error processing Chart.js for iframe:', error);
			}
		}

		// If nothing to inject, return original HTML
		if (scriptTags.length === 0) return html;

		const tags = scriptTags.join('\n');

		// Prefer injecting into <head>, then before </body>, otherwise prepend
		if (html.includes('</head>')) {
			return html.replace('</head>', `${tags}\n</head>`);
		}
		if (html.includes('</body>')) {
			return html.replace('</body>', `${tags}\n</body>`);
		}
		return `${tags}\n${html}`;
	}

	// Try to measure same-origin content safely
	function resizeSameOrigin() {
		if (!iframe) return;
		try {
			const doc = iframe.contentDocument || iframe.contentWindow?.document;
			if (!doc) return;
			const h = Math.max(doc.documentElement?.scrollHeight ?? 0, doc.body?.scrollHeight ?? 0);
			if (h > 0) iframe.style.height = h + 20 + 'px';
		} catch {
			// Cross-origin → rely on postMessage from inside the iframe
		}
	}

	// Track whether we've responded to MCP Apps init
	let mcpInitDone = false;

	function onMessage(e: MessageEvent) {
		if (!iframe || e.source !== iframe.contentWindow) return;

		const data = e.data || {};
		if (data?.type === 'iframe:height' && typeof data.height === 'number') {
			iframe.style.height = Math.max(0, data.height) + 'px';
		}

		// MCP Apps: respond to ui/initialize so the SDK's connect() resolves
		// and features like autoResize activate.
		// Only process MCP messages when isMcpApp is true (set from component
		// props) to prevent non-MCP iframes from hijacking the tool relay.
		if (
			isMcpApp &&
			data?.jsonrpc === '2.0' &&
			data?.method === 'ui/initialize' &&
			data?.id != null &&
			!mcpInitDone
		) {
			mcpInitDone = true;
			iframe.contentWindow?.postMessage(
				{
					jsonrpc: '2.0',
					id: data.id,
					result: {
						protocolVersion: data?.params?.protocolVersion || '2026-01-26',
						hostInfo: { name: 'Open WebUI', version: '1.0.0' },
						hostCapabilities: {
							serverTools: { listChanged: false },
							updateModelContext: { text: {} }
						},
						hostContext: {
							containerDimensions: { height: 600 }
						}
					}
				},
				'*'
			);
			// Send tool-input notification with the tool arguments
			// so apps waiting on ontoolinput can proceed to render.
			// args may be double-encoded (JSON string of a JSON string) from
			// the middleware's html.escape(json.dumps(json.dumps(arguments))).
			// Unwrap until we get an object.
			let toolArgs: Record<string, unknown> = {};
			try {
				let parsed: unknown = args
					? typeof args === 'string'
						? JSON.parse(args)
						: args
					: {};
				while (typeof parsed === 'string') {
					parsed = JSON.parse(parsed);
				}
				if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
					toolArgs = parsed as Record<string, unknown>;
				}
			} catch {
				toolArgs = {};
			}
			setTimeout(() => {
				iframe.contentWindow?.postMessage(
					{
						jsonrpc: '2.0',
						method: 'ui/notifications/tool-input',
						params: { arguments: toolArgs }
					},
					'*'
				);
				// Send tool-result from the parent via postMessage so the SDK
				// transport accepts it (synthetic dispatchEvent can fail).
				if (toolResult) {
					setTimeout(() => {
						const params: Record<string, unknown> = {
							content: [{ type: 'text', text: toolResult }]
						};
						try {
							const parsed = JSON.parse(toolResult);
							if (parsed && typeof parsed === 'object') {
								params.structuredContent = parsed;
							}
						} catch {
							// not JSON, that's fine
						}
						iframe.contentWindow?.postMessage(
							{ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params },
							'*'
						);
					}, 50);
				}
			}, 10);
		}

		// MCP Apps: relay tools/call to the backend MCP API,
		// handle ui/update-model-context, or return error for
		// other unsupported JSON-RPC requests.
		if (
			isMcpApp &&
			data?.jsonrpc === '2.0' &&
			data?.id != null &&
			data?.method &&
			data.method !== 'ui/initialize'
		) {
			if (data.method === 'tools/call' && serverId) {
				const token = localStorage?.token;
				if (token) {
					const requestId = data.id;
					fetch(`/api/v1/mcp/tool/call`, {
						method: 'POST',
						headers: {
							Authorization: `Bearer ${token}`,
							'Content-Type': 'application/json'
						},
						body: JSON.stringify({
							server_id: serverId,
							tool_name: data.params?.name,
							arguments: data.params?.arguments ?? {}
						})
					})
						.then((r) => {
							if (!r.ok) {
								return r.json().then((body: Record<string, unknown>) => {
									throw new Error((body?.detail as string) ?? `HTTP ${r.status}`);
								});
							}
							return r.json();
						})
						.then((result) => {
							// Strip null values — the Python MCP SDK serializes
							// Optional fields as null, but the TypeScript SDK's
							// Zod schemas use z.optional() which rejects null.
							if (result && typeof result === 'object') {
								if (Array.isArray(result.content)) {
									result.content = result.content.map(
										(item: Record<string, unknown>) => {
											const clean: Record<string, unknown> = {};
											for (const [k, v] of Object.entries(item)) {
												if (v !== null) clean[k] = v;
											}
											return clean;
										}
									);
								}
								for (const key of Object.keys(result)) {
									if (result[key] === null) delete result[key];
								}
							}
							iframe.contentWindow?.postMessage(
								{ jsonrpc: '2.0', id: requestId, result },
								'*'
							);
						})
						.catch(() => {
							iframe.contentWindow?.postMessage(
								{
									jsonrpc: '2.0',
									id: requestId,
									error: { code: -32603, message: 'Tool call failed' }
								},
								'*'
							);
						});
				} else {
					iframe.contentWindow?.postMessage(
						{
							jsonrpc: '2.0',
							id: data.id,
							error: { code: -32603, message: 'Not authenticated' }
						},
						'*'
					);
				}
			} else if (data.method === 'ui/update-model-context') {
				const content = data.params?.content || [];
				const textParts = content
					.filter((c: { type: string; text?: string }) => c.type === 'text' && c.text)
					.map((c: { type: string; text?: string }) => c.text as string);
				const contextText = textParts.join('\n');
				if (contextText) {
					dispatch('modelcontext', { text: contextText });
				}
				iframe.contentWindow?.postMessage(
					{ jsonrpc: '2.0', id: data.id, result: {} },
					'*'
				);
			} else {
				iframe.contentWindow?.postMessage(
					{
						jsonrpc: '2.0',
						id: data.id,
						error: { code: -32601, message: 'Method not supported in embed mode' }
					},
					'*'
				);
			}
		}

		// MCP Apps: handle ui/notifications/size-changed
		// Ignore height: 0 — apps with 100vh layouts report 0 before
		// the iframe has an initial height to fill.
		if (
			isMcpApp &&
			data?.jsonrpc === '2.0' &&
			data?.method === 'ui/notifications/size-changed' &&
			typeof data?.params?.height === 'number' &&
			data.params.height > 0
		) {
			iframe.style.height = data.params.height + 'px';
		}

		// Pong message for testing connectivity
		if (data?.type === 'pong') {
			// Optional: reply back
			iframe.contentWindow?.postMessage({ type: 'pong:ack' }, '*');
		}

		// Send payload data if requested
		if (data?.type === 'payload') {
			iframe.contentWindow?.postMessage(
				{ type: 'payload', requestId: data?.requestId ?? null, payload: payload },
				'*'
			);
		}
	}

	// When the iframe loads, try same-origin resize (cross-origin will noop)
	const onLoad = async () => {
		requestAnimationFrame(resizeSameOrigin);

		// if arguments are provided, inject them into the iframe window
		// (only works with allow-same-origin, silently skipped for cross-origin)
		if (args && iframe?.contentWindow) {
			try {
				(iframe.contentWindow as any).args = args;
			} catch {
				// cross-origin sandbox — args are delivered via postMessage instead
			}
		}
	};

	// Ensure event listener bound only while component lives
	onMount(() => {
		window.addEventListener('message', onMessage);
	});

	onDestroy(() => {
		window.removeEventListener('message', onMessage);
	});
</script>

{#if iframeDoc}
	<iframe
		bind:this={iframe}
		srcdoc={iframeDoc}
		{title}
		class={iframeClassName}
		style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
		width="100%"
		frameborder="0"
		{sandbox}
		{allowFullscreen}
		on:load={onLoad}
	/>
{:else if iframeSrc}
	<iframe
		bind:this={iframe}
		src={iframeSrc}
		{title}
		class={iframeClassName}
		style={`${initialHeight ? `height:${initialHeight}px;` : ''}`}
		width="100%"
		frameborder="0"
		{sandbox}
		referrerpolicy={referrerPolicy}
		{allowFullscreen}
		on:load={onLoad}
	/>
{/if}
