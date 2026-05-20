# otari-sandbox-container

Code-execution sandbox container — a Python REPL behind a multi-session HTTP
API. Pairs with [mozilla-ai/gateway](https://github.com/mozilla-ai/gateway) to
provide `code_execution` tool support, but the contract is plain HTTP so any
client that speaks it can use the container directly.

Built images are published to GitHub Container Registry:

```
ghcr.io/mozilla-ai/otari-sandbox-container:latest
ghcr.io/mozilla-ai/otari-sandbox-container:<sha>
```

Each session has its own `/var/sandbox/sessions/<id>/` tree with a private
workspace and Python REPL subprocess. State (variables, imports) persists
across `/exec` calls within a session and is wiped on `DELETE /sessions/{id}`.

The wire shapes returned by `POST /exec` match Anthropic's
`code_execution_20250825` content blocks (`code_execution_tool_result`,
`bash_code_execution_tool_result`, `text_editor_code_execution_tool_result`)
so consumers that already parse Anthropic shapes work without translation.

## Layout

```
sandbox/
  models.py           # Pydantic shapes (request, result blocks)
  runner.py           # Long-lived Python REPL with sentinel protocol
  exec_server.py      # FastAPI app: /sessions, /exec, /files, /health
  text_editor.py      # view/create/str_replace/insert/undo_edit handlers
tests/
Dockerfile            # python:3.12-slim base, pinned package set
Makefile              # build, test, run
```

## Local development

```sh
make install   # uv sync
make test      # run pytest
make build     # build the Docker image (otari-sandbox-container:dev)
make run       # docker run -p 8080:8080
```

## Pulling the published image

```sh
docker pull ghcr.io/mozilla-ai/otari-sandbox-container:latest
docker run --rm -p 8080:8080 ghcr.io/mozilla-ai/otari-sandbox-container:latest
```

## API

```
POST   /sessions                       -> create session
GET    /sessions/{id}                  -> session metadata
POST   /sessions/{id}/exec             -> run code in session
DELETE /sessions/{id}                  -> destroy session
GET    /sessions/{id}/files            -> download a file from the workspace
GET    /sessions/{id}/files/list       -> list workspace files
GET    /health                         -> 200 if server alive
```

A streaming exec endpoint (SSE stdout/stderr deltas) is a planned follow-up;
the current `/exec` is request/response only.

The full request/response schemas live in `sandbox/models.py`.

## Security notes

- The container runs as a non-root user (uid 1000).
- One container hosts many isolated sessions; sessions can't see each
  other's workspaces.
- `/var/sandbox/sessions` is intended to be mounted as tmpfs in production
  so session state stays ephemeral.
- **Don't expose the HTTP port to untrusted networks.** There's no
  authentication on the API — the assumption is that the gateway (or
  another trusted component) is the sole client, reached over a private
  Docker network or similar. The gateway's `docker-compose.yml` binds the
  sandbox port to `127.0.0.1` for this reason.
