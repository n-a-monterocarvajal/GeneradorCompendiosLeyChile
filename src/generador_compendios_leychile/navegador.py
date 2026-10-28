"""Consultas a los servicios JSON de Ley Chile desde Chromium.

Para robustecer el método de obtención, la revisión y la generación consultan los servicios
JSON de Ley Chile a través de un contexto de navegador, el mismo tipo de cliente con que la
página de la norma pide esos datos.

``SesionNavegador`` expone el ``get`` de ``requests.Session`` que usan ``fetch_snapshot`` y
``fetch_modifying_norms``, y traduce las fallas de red de Playwright a las de ``requests``
para que sigan clasificándose como transitorias.
"""

from __future__ import annotations

import json
from typing import Any

import requests
from requests.structures import CaseInsensitiveDict

PAGINA_INICIAL = "https://www.bcn.cl/leychile/navegar?idNorma={id_norma}"


class RespuestaNavegador:
    """Lo que ``fetch_snapshot`` y ``_get_json`` leen de una respuesta de ``requests``."""

    def __init__(self, status_code: int, headers: dict[str, str], text: str) -> None:
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers)
        self.text = text

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as error:
            raise requests.JSONDecodeError(error.msg, error.doc, error.pos) from error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class SesionNavegador:
    """Sesión de consultas JSON a Ley Chile sobre un contexto de Chromium.

    Sin ``context`` abre su propio Chromium al entrar y lo cierra al salir. Con ``context``
    usa el de la generación del compendio y no lo cierra.
    """

    def __init__(self, context: Any = None, id_norma_inicial: str | None = None) -> None:
        self._context = context
        self._propio = context is None
        self._id_norma_inicial = id_norma_inicial
        self._playwright: Any = None
        self._browser: Any = None

    def __enter__(self) -> "SesionNavegador":
        if self._propio:
            from playwright.sync_api import sync_playwright

            from .generador import HEADERS

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
            self._context = self._browser.new_context(user_agent=HEADERS["User-Agent"], locale="es-CL")
            if self._id_norma_inicial:
                self._abrir_norma(self._id_norma_inicial)
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _abrir_norma(self, id_norma: str) -> None:
        # Abre la página de la norma como lo haría una persona antes de que la página pida
        # el JSON. Es de mejor esfuerzo: si no carga, las consultas se intentan igual.
        page = self._context.new_page()
        try:
            page.goto(PAGINA_INICIAL.format(id_norma=id_norma), wait_until="domcontentloaded", timeout=90000)
        except Exception:
            pass
        finally:
            page.close()

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 60) -> RespuestaNavegador:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        cabeceras = {"Accept": "application/json,text/plain;q=0.9,*/*;q=0.8"} | (headers or {})
        try:
            response = self._context.request.get(url, headers=cabeceras, timeout=timeout * 1000)
            return RespuestaNavegador(response.status, response.headers, response.text())
        except PlaywrightTimeout as error:
            raise requests.Timeout(str(error)) from error
        except PlaywrightError as error:
            raise requests.ConnectionError(str(error)) from error

    def close(self) -> None:
        if self._propio:
            if self._browser is not None:
                self._browser.close()
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
