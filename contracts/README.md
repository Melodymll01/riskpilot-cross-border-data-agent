# Go / Python contracts

Phase 1 uses the existing `/api/v2` HTTP and SSE contracts through the Go entry. Go implements authentication and session handling. Python accepts business requests only with a Go-issued `X-Riskpilot-Identity` JWT.

The [identity schema](internal-identity-v1.schema.json) defines the claims. Tokens use HS256 with a separate internal service key, bind method and escaped request target, expire within 30 seconds, and include a one-time nonce. The Python runtime verifies the account exists and rejects reused nonces within the accepting process. The first phase runs one Python worker; it does not claim a distributed replay cache.

The Go entry removes browser `Cookie`, `Authorization`, forwarding headers and all incoming `X-Riskpilot-*` headers from upstream requests. It signs a new identity from the verified session and does not forward upstream session cookies to the browser. Python identity validation does not consume browser cookies or create/update user records.

This token authenticates the internal caller and resource owner. It does not validate model answers or prove that a body contains an authorized resource; existing owner checks and Evidence QA still apply. TLS or a trusted local network protects transport. Local Python binds loopback; Compose does not publish its port.

Session JWTs preserve the previous HS256 `sub/iat/exp` format and signing key so existing cookies remain usable. GitHub identities are matched by provider ID, preserving old owner IDs across username changes. New GitHub users use `github:id:<numeric ID>`.

Go authentication state is saved in SQLite as a hash with an expiry and bound to an HttpOnly browser cookie. The state is consumed atomically and survives Go restarts. Auth events use `gateway_auth_events`; the existing Python audit endpoint does not yet merge this stream.

The real Go/Python test is [test_go_gateway_integration.py](../tests/infra/test_go_gateway_integration.py), including a browser scenario. It complements the Go auth/proxy tests and Python signature/scope tests; it does not execute real GitHub OAuth or a remote model.
