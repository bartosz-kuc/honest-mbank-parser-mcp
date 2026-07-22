# Security Policy

## Reporting a vulnerability

If you find a security issue in this MCP server, please email **firma@bartosza.pl**. Do **not** open a public GitHub issue.

- I aim to respond within 48 hours. This is a solo hobby project — there is no formal SLA — but security reports are the fastest path to my attention.
- Please include: version / commit hash affected, steps to reproduce, and the impact scenario.
- PGP key available on request. Otherwise, plain email is fine.

## Scope

This server is one member of the [honest-mcp family](https://github.com/bartosz-kuc?tab=repositories). If your report affects the family pattern broadly, I will coordinate cross-repository disclosure.

**In scope:**
- Code execution paths in `server.py` that could be triggered by AI-client input
- Handling of authentication material (OAuth tokens, API keys, session state)
- Any path that could exfiltrate data past the documented "AI ↔ MCP server ↔ upstream API" trust boundary
- Dependency vulnerabilities that materially expand attack surface

**Not in scope:**
- Whatever your AI client itself can reach — that is the intended data flow. Trust boundaries are between your AI client and the external service the server wraps.
- Local credential files (e.g. `credentials.json`, `token.json`) that you generate on your own machine per the setup instructions. These are your responsibility; the shipped `.gitignore` prevents accidental commits.
- Denial-of-service via upstream rate limits (each server documents which public APIs it hits).

## Supported versions

Only the current `main` branch is supported. Releases are tagged from `main` and any accepted security fix will be published as a new tagged patch release.

## Automated safeguards enabled on this repo

- Dependabot alerts and version updates (weekly)
- Automated security fixes for published CVEs in dependencies
- Secret scanning + push protection (blocks accidental credential commits)
- CodeQL static analysis on push and PR
