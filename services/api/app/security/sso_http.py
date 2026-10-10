"""Bounded, TLS-verified transport for operator-configured identity providers."""
import asyncio
import httpx2 as httpx

MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_SECONDS = 20


class LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream, deadline):
        self.stream, self.deadline = stream, deadline

    async def __aiter__(self):
        count = 0
        try:
            async with asyncio.timeout_at(self.deadline):
                async for chunk in self.stream:
                    count += len(chunk)
                    if count > MAX_RESPONSE_BYTES:
                        raise ValueError("Identity provider response exceeds 1 MiB")
                    yield chunk
        finally:
            await self.aclose()

    async def aclose(self):
        await self.stream.aclose()


class SsoTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.transport = httpx.AsyncHTTPTransport(verify=True, trust_env=False)

    async def handle_async_request(self, request):
        deadline = asyncio.get_running_loop().time() + REQUEST_SECONDS
        request.headers['Accept-Encoding'] = 'identity'
        async with asyncio.timeout_at(deadline):
            response = await self.transport.handle_async_request(request)
        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
            await response.aclose()
            raise ValueError("Identity provider must honour Accept-Encoding: identity")
        response.stream = LimitedStream(response.stream, deadline)
        return response

    async def aclose(self):
        await self.transport.aclose()


def client_options():
    return dict(timeout=10.0, verify=True, trust_env=False, follow_redirects=False,
                transport=SsoTransport())
