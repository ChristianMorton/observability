"""Regression checks for fail-closed MCP authentication (no telemetry services)."""
import importlib.util
import os
import unittest
from pathlib import Path
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

class AuthTests(unittest.TestCase):
    def check_mode(self, key):
        old = os.environ.get('MCP_API_KEY')
        os.environ['MCP_API_KEY'] = key
        try:
            spec = importlib.util.spec_from_file_location('obs_test_server', Path(__file__).resolve().parents[1] / 'mcp-server/server.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            async def protected(request): return PlainTextResponse('protected')
            module.app.router.routes.insert(0, Route('/protected', protected))
            client = TestClient(module.app)
            self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(client.get('/protected').status_code, 401)
            self.assertEqual(client.get('/protected', headers={'Authorization':'Bearer wrong'}).status_code, 401)
            expected = 200 if key else 401
            self.assertEqual(client.get('/protected', headers={'Authorization':f'Bearer {key}'}).status_code, expected)
            if key:
                self.assertEqual(client.get('/protected', headers={'X-API-Key':key}).status_code, 200)
        finally:
            if old is None: os.environ.pop('MCP_API_KEY', None)
            else: os.environ['MCP_API_KEY'] = old
    def test_no_key_fails_closed(self): self.check_mode('')
    def test_configured_key(self): self.check_mode('test-only-key')

if __name__ == '__main__': unittest.main()
