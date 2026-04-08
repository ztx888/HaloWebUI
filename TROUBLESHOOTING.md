# Open WebUI Troubleshooting Guide

## Understanding the Open WebUI Architecture

The Open WebUI system is designed to streamline interactions between the client (your browser) and the Ollama API. At the heart of this design is a backend reverse proxy, enhancing security and resolving CORS issues.

- **How it Works**: The Open WebUI is designed to interact with the Ollama API through a specific route. When a request is made from the WebUI to Ollama, it is not directly sent to the Ollama API. Initially, the request is sent to the Open WebUI backend via `/ollama` route. From there, the backend is responsible for forwarding the request to the Ollama API. This forwarding is accomplished by using the route specified in the `OLLAMA_BASE_URL` environment variable. Therefore, a request made to `/ollama` in the WebUI is effectively the same as making a request to `OLLAMA_BASE_URL` in the backend. For instance, a request to `/ollama/api/tags` in the WebUI is equivalent to `OLLAMA_BASE_URL/api/tags` in the backend.

- **Security Benefits**: This design prevents direct exposure of the Ollama API to the frontend, safeguarding against potential CORS (Cross-Origin Resource Sharing) issues and unauthorized access. Requiring authentication to access the Ollama API further enhances this security layer.

## Open WebUI: Server Connection Error

If you're experiencing connection issues, it’s often due to the WebUI docker container not being able to reach the Ollama server at 127.0.0.1:11434 (host.docker.internal:11434) inside the container . Use the `--network=host` flag in your docker command to resolve this. Note that the port changes from 3000 to 8080, resulting in the link: `http://localhost:8080`.

**Example Docker Command**:

```bash
docker run -d --network=host -v open-webui:/app/backend/data -e OLLAMA_BASE_URL=http://127.0.0.1:11434 --name open-webui --restart always ghcr.io/ztx888/halowebui:main
```

### Error on Slow Responses for Ollama

Open WebUI has a default timeout of 5 minutes for Ollama to finish generating the response. If needed, this can be adjusted via the environment variable AIOHTTP_CLIENT_TIMEOUT, which sets the timeout in seconds.

### General Connection Errors

**Ensure Ollama Version is Up-to-Date**: Always start by checking that you have the latest version of Ollama. Visit [Ollama's official site](https://ollama.com/) for the latest updates.

**Troubleshooting Steps**:

1. **Verify Ollama URL Format**:
   - When running the Web UI container, ensure the `OLLAMA_BASE_URL` is correctly set. (e.g., `http://192.168.1.1:11434` for different host setups).
   - In the Open WebUI, navigate to "Settings" > "General".
   - Confirm that the Ollama Server URL is correctly set to `[OLLAMA URL]` (e.g., `http://localhost:11434`).

By following these enhanced troubleshooting steps, connection issues should be effectively resolved. For further assistance or queries, feel free to reach out to us on our community Discord.

## Anthropic Proxy: Anyrouter + `claude-opus-4-6`

### Symptom

`POST /v1/messages` returns:

`invalid claude code request`

### Root Cause (Observed)

For Anyrouter, `claude-opus-4-6` may be validated with Claude Code-like request body rules.
Using only the normal OpenAI-compatible converted payload can be rejected even when API key and headers are valid.

### Current Fix in This Repo

In `backend/open_webui/routers/anthropic.py`, when:

- base URL host contains `anyrouter`, and
- model is exactly `claude-opus-4-6`

the backend now normalizes `system` blocks before sending upstream:

- `system[0].text` is forced to:
  `You are a Claude agent, built on Anthropic's Claude Agent SDK.`
- `system[1].text` is guaranteed non-empty (fallback: `Follow the user instructions.`)
- existing later `system` content is preserved

Also, model ids are now preserved as configured for Anthropic proxies by default.
We do **not** rewrite `claude-opus-4-6` to a dated alias like `claude-opus-4-6-20250918`,
because many relays only expose the short alias or their own custom suffixes.

### Maintenance Note

If you refactor Anthropic payload conversion, keep this Anyrouter-specific normalization
or you may reintroduce `invalid claude code request` for `claude-opus-4-6`.

Users do not need to manually enable any Claude Code fingerprint settings — the system
handles Anyrouter compatibility automatically via system signature normalization.
