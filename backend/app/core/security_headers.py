"""API security headers applied independently of route behavior."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_BASE_HEADERS = {
    b"content-security-policy": (
        b"default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    b"permissions-policy": b"camera=(), geolocation=(), microphone=()",
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
}


class SecurityHeadersMiddleware:
    """Attach a restrictive baseline suitable for the JSON API."""

    def __init__(self, app: ASGIApp, *, enable_hsts: bool) -> None:
        self.app = app
        self.headers = dict(_BASE_HEADERS)
        if enable_hsts:
            self.headers[b"strict-transport-security"] = (
                b"max-age=31536000; includeSubDomains; preload"
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                headers.extend(
                    (name, value) for name, value in self.headers.items() if name not in existing
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
