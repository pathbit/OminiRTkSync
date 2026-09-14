"""Testes de resiliência do servidor web: desconexão do cliente e custo do /healthz."""

import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request

from omini_rtksync.config import Settings
from omini_rtksync import web as web_server


class TestHealthzResilience(unittest.TestCase):
    """Cobre o BrokenPipeError do health check do Docker e o custo da sondagem ao gateway."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp_dir.name, "storage.sqlite")
        with sqlite3.connect(cls.db_path) as conn:
            conn.execute(
                "CREATE TABLE provider_connections (id TEXT PRIMARY KEY, provider TEXT, name TEXT, "
                "access_token TEXT, refresh_token TEXT, api_key TEXT, expires_at TEXT, "
                "test_status TEXT, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE model_combos (id TEXT PRIMARY KEY, name TEXT, models TEXT, "
                "created_at TEXT, updated_at TEXT)"
            )

        cls.settings = Settings(
            db_path=cls.db_path,
            web_host="127.0.0.1",
            web_port=19391,
            dashboard_user="admin",
            dashboard_password="senha-de-teste",
        )
        cls.server = web_server.start_web_server(
            "127.0.0.1", 19391, cls.db_path, omniroute_url="", settings=cls.settings
        )
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    def setUp(self):
        web_server._gateway_probe_cache.clear()

    def test_server_is_multi_threaded(self):
        """Sem multi-thread, uma requisição lenta bloqueia o health check do Docker."""
        self.assertIsInstance(self.server, web_server.QuietThreadingHTTPServer)
        self.assertTrue(self.server.daemon_threads)

    def test_healthz_responds_ok(self):
        with urllib.request.urlopen("http://127.0.0.1:19391/healthz", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"OK")

    def test_healthz_sends_exactly_one_well_formed_response(self):
        """O teste anterior so olhava corpo e status, e por isso passava enquanto o
        servidor levantava NameError depois de escrever a resposta: o cliente ja
        tinha recebido tudo. Content-Length prova que houve uma unica resposta
        completa, montada antes de qualquer escrita."""
        with urllib.request.urlopen(f"http://127.0.0.1:{19391}/healthz", timeout=5) as resp:
            body = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, b"OK")
            self.assertEqual(int(resp.headers["Content-Length"]), len(body))
            self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_healthz_does_not_raise_inside_the_handler(self):
        """Uma excecao apos a resposta nao aparece para o cliente, so no log do
        container. Sondar duas vezes garante que o handler termina inteiro."""
        for _ in range(2):
            with urllib.request.urlopen(f"http://127.0.0.1:{19391}/healthz", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        self.assertGreater(self.server.socket.fileno(), 0)

    def test_client_disconnect_does_not_crash_the_server(self):
        """O probe do Docker fecha o socket cedo; isso não pode virar traceback nem derrubar o servidor."""
        for _ in range(5):
            s = socket.create_connection(("127.0.0.1", 19391), timeout=5)
            s.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            # Fecha imediatamente, sem ler a resposta: é exatamente o que produzia
            # BrokenPipeError: [Errno 32] em serve_healthz.
            s.close()

        time.sleep(0.3)

        # O servidor tem que continuar atendendo normalmente depois disso.
        with urllib.request.urlopen("http://127.0.0.1:19391/healthz", timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_concurrent_requests_are_served_in_parallel(self):
        results = []

        def hit():
            try:
                with urllib.request.urlopen("http://127.0.0.1:19391/healthz", timeout=5) as r:
                    results.append(r.status)
            except Exception as e:  # pragma: no cover - falha explícita no assert abaixo
                results.append(str(e))

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(results, [200] * 8)


class TestQuietHandleError(unittest.TestCase):
    """handle_error nao pode imprimir traceback quando o cliente apenas desconectou.

    Regressao do erro reportado em producao:
        File ".../web/server.py", line 116, in serve_healthz
            self.wfile.write(b"OK")
        BrokenPipeError: [Errno 32] Broken pipe
    """

    def _server_stub(self):
        # Instancia sem __init__ para nao abrir socket: so exercita handle_error.
        return web_server.QuietThreadingHTTPServer.__new__(web_server.QuietThreadingHTTPServer)

    def _capture(self, exc):
        server = self._server_stub()
        printed = []
        original = web_server.ThreadingHTTPServer.handle_error
        web_server.ThreadingHTTPServer.handle_error = lambda *a, **k: printed.append(True)
        try:
            try:
                raise exc
            except type(exc):
                server.handle_error(None, ("127.0.0.1", 46392))
        finally:
            web_server.ThreadingHTTPServer.handle_error = original
        return printed

    def test_broken_pipe_is_swallowed(self):
        self.assertEqual(self._capture(BrokenPipeError(32, "Broken pipe")), [])

    def test_connection_reset_is_swallowed(self):
        self.assertEqual(self._capture(ConnectionResetError(104, "Connection reset by peer")), [])

    def test_real_errors_still_reach_the_default_handler(self):
        self.assertEqual(self._capture(ValueError("falha de verdade")), [True])

    def test_write_body_survives_a_broken_pipe(self):
        class ExplodingWriter:
            def write(self, _payload):
                raise BrokenPipeError(32, "Broken pipe")

        handler = web_server.DashboardHandler.__new__(web_server.DashboardHandler)
        handler.wfile = ExplodingWriter()
        handler.close_connection = False

        handler.write_body(b"OK")  # nao pode propagar
        self.assertTrue(handler.close_connection)


class TestGatewayProbeCache(unittest.TestCase):
    """A sondagem ao gateway não pode acontecer a cada probe: ela faz I/O de rede de até 3s."""

    def setUp(self):
        web_server._gateway_probe_cache.clear()
        self.calls = []

    def tearDown(self):
        web_server._gateway_probe_cache.clear()

    def _handler_with_fake_probe(self, url: str):
        calls = self.calls

        class FakeHandler(web_server.DashboardHandler):
            router_url = url

            def __init__(self):  # não instancia socket: só exercita probe_gateway
                pass

        original_urlopen = web_server.urllib.request.urlopen

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(*args, **kwargs):
            calls.append(1)
            return FakeResponse()

        web_server.urllib.request.urlopen = fake_urlopen
        self.addCleanup(lambda: setattr(web_server.urllib.request, "urlopen", original_urlopen))
        return FakeHandler()

    def test_probe_result_is_cached(self):
        handler = self._handler_with_fake_probe("http://gateway.invalido:20128")

        for _ in range(10):
            self.assertTrue(handler.probe_gateway())

        # 10 chamadas ao /healthz, uma única ida à rede.
        self.assertEqual(len(self.calls), 1)

    def test_cache_expires_after_the_ttl(self):
        handler = self._handler_with_fake_probe("http://gateway.invalido:20128")
        handler.probe_gateway()

        # Envelhece a entrada de cache além do TTL.
        cached_at, cached_ok = web_server._gateway_probe_cache["http://gateway.invalido:20128"]
        web_server._gateway_probe_cache["http://gateway.invalido:20128"] = (
            cached_at - web_server.GATEWAY_PROBE_TTL_SECONDS - 1,
            cached_ok,
        )

        handler.probe_gateway()
        self.assertEqual(len(self.calls), 2)

    def test_no_gateway_url_means_no_network_call(self):
        handler = self._handler_with_fake_probe("")
        self.assertTrue(handler.probe_gateway())
        self.assertEqual(len(self.calls), 0)


if __name__ == "__main__":
    unittest.main()
