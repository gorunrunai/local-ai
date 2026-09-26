# Security policy

GoRunRun Local AI runs models, a sandboxed code runner and optional MCP servers on your Mac, so security reports matter to us.

## Reporting a vulnerability
Please **do not open a public issue**. Report it privately through GitHub: **Security → Report a vulnerability** on https://github.com/gorunrunai/local-ai. Include steps to reproduce and the impact. We aim to acknowledge reports within a few days.

## In scope
- Escaping the `code_exec` sandbox, or tools writing outside their folders
- Reaching the app from another machine without `REMOTE_ACCESS` and the token
- Prompt injection from files, web pages or tool output that runs tools without the user's approval
- Server-side request forgery through `web_fetch`
- Artifacts escaping their sandboxed frame

## Supported versions
Security fixes land on `main`; please update to the latest version.
