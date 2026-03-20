"""Custom middleware for SCORE."""


class ContentSecurityPolicyMiddleware:
    """Add a Content-Security-Policy header to every response.

    Allows CDN resources used by the frontend (Bootstrap, D3, marked.js,
    pdf.js, echarts, Hotwire) while blocking everything else.
    """

    CSP = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com",
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net",
            "img-src 'self' data: blob:",
            "font-src 'self' cdn.jsdelivr.net",
            "connect-src 'self' login.microsoftonline.com",
            "form-action 'self' login.microsoftonline.com",
            "worker-src 'self' blob:",
            "frame-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
        ]
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = self.CSP
        return response
