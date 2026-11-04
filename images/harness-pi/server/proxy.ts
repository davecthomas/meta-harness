/**
 * Sends the harness server's own HTTP calls through HTTP_PROXY / HTTPS_PROXY.
 *
 * In a per-run sandbox the only way out is the egress proxy, so the model
 * client's calls to the HTTP Gateway must go through it. Node 22 honors those
 * variables when NODE_USE_ENV_PROXY=1, but loading pi's SDK installs undici's
 * plain dispatcher in its place, and fetch then resolves hosts directly. pi's
 * own command-line entry point installs a proxy-aware dispatcher; the SDK does
 * not. This does the same, with the settings pi uses.
 *
 * Call it after pi's SDK has loaded. It does nothing when no proxy is set.
 */

import { EnvHttpProxyAgent, install, setGlobalDispatcher } from "undici";

export function useEnvProxy(log: (text: string) => void): void {
	const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || process.env.HTTP_PROXY || process.env.http_proxy;
	if (!proxy) return;
	setGlobalDispatcher(new EnvHttpProxyAgent({ allowH2: false, proxyTunnel: true }));
	// Keep fetch on the same undici as the dispatcher, as pi's entry point does.
	install?.();
	log(`outbound HTTP goes through ${proxy} (NO_PROXY: ${process.env.NO_PROXY || process.env.no_proxy || "none"})`);
}
