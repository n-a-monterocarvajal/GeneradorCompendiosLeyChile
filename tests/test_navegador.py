import unittest

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from generador_compendios_leychile.navegador import RespuestaNavegador, SesionNavegador
from generador_compendios_leychile.versiones import FallaTransitoriaLeyChile, fetch_snapshot


class FakeApiResponse:
    def __init__(self, status=200, text="{}", content_type="application/json"):
        self.status = status
        self.headers = {"content-type": content_type}
        self._text = text

    def text(self):
        return self._text


class FakeRequest:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeContext:
    def __init__(self, *outcomes):
        self.request = FakeRequest(*outcomes)


class RespuestaNavegadorTests(unittest.TestCase):
    def test_reads_json_and_headers_case_insensitive(self):
        response = RespuestaNavegador(200, {"content-type": "application/json"}, '{"a": 1}')
        self.assertEqual({"a": 1}, response.json())
        self.assertEqual("application/json", response.headers["Content-Type"])

    def test_invalid_json_raises_requests_error(self):
        with self.assertRaises(requests.JSONDecodeError):
            RespuestaNavegador(200, {}, "<html></html>").json()

    def test_http_error_status(self):
        with self.assertRaises(requests.HTTPError):
            RespuestaNavegador(404, {}, "").raise_for_status()


class SesionNavegadorTests(unittest.TestCase):
    def test_get_passes_headers_and_timeout_in_ms(self):
        context = FakeContext(FakeApiResponse(text='{"ok": true}'))
        response = SesionNavegador(context).get("https://x", headers={"Referer": "r"}, timeout=90)
        self.assertEqual({"ok": True}, response.json())
        _, headers, timeout = context.request.calls[0]
        self.assertEqual("r", headers["Referer"])
        self.assertIn("application/json", headers["Accept"])
        self.assertEqual(90000, timeout)

    def test_playwright_failures_map_to_requests(self):
        with self.assertRaises(requests.Timeout):
            SesionNavegador(FakeContext(PlaywrightTimeout("lento"))).get("https://x")
        with self.assertRaises(requests.ConnectionError):
            SesionNavegador(FakeContext(PlaywrightError("sin red"))).get("https://x")

    def test_network_failures_are_transient_in_review(self):
        session = SesionNavegador(FakeContext(*(PlaywrightError("sin red") for _ in range(3))))
        with self.assertRaises(FallaTransitoriaLeyChile):
            fetch_snapshot(session, "1984", "Código Penal", base_wait=0)

    def test_html_page_is_transient_in_review(self):
        page = FakeApiResponse(text="<!doctype html><html></html>", content_type="text/html")
        session = SesionNavegador(FakeContext(page, page, page))
        with self.assertRaises(FallaTransitoriaLeyChile):
            fetch_snapshot(session, "1984", "Código Penal", base_wait=0)

    def test_external_context_is_not_closed(self):
        session = SesionNavegador(FakeContext())
        with session:
            pass
        self.assertIsNotNone(session._context)


if __name__ == "__main__":
    unittest.main()
