from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from pypdf import PdfReader, PdfWriter

from .config import safe_name

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.7",
}


def output_name_from_base(base_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M_UTC")
    return f"{safe_name(base_name)}_{timestamp}.pdf"


def assert_pdf(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise RuntimeError(f"El archivo no existe o es demasiado pequeño: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise RuntimeError(f"No parece ser un PDF válido: {path}")
    PdfReader(str(path))


def save_debug(page: Page, directory: Path, prefix: str, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"debug_{safe_name(prefix)}_{safe_name(name)}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception:
        pass
    try:
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8", errors="ignore")
    except Exception:
        pass


def session_from_context(context: BrowserContext, referer: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS | {"Referer": referer})
    for cookie in context.cookies():
        try:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie.get("domain"), path=cookie.get("path", "/"),
            )
        except Exception:
            continue
    return session


def find_export_links(page: Page) -> list[str]:
    return page.evaluate(
        r"""
        () => {
            const abs = (u) => {
                try { return new URL(u, document.baseURI).href; }
                catch(e) { return null; }
            };
            const out = [];
            for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                const href = a.href || a.getAttribute('href') || '';
                const text = [
                    a.innerText || '', a.getAttribute('title') || '',
                    a.getAttribute('aria-label') || '', a.outerHTML || ''
                ].join(' ');
                const u = abs(href);
                if (!u) continue;
                if (/\/servicios\/Consulta\/Exportar/i.test(u) && /exportar_formato=pdf/i.test(u) && /radioExportar=Normas/i.test(u)) {
                    let score = 0;
                    if (/Descargar\s+PDF\s+de\s+esta\s+norma/i.test(text)) score += 1000;
                    if (/Descargar|download|fa-download/i.test(text)) score += 300;
                    out.push({u, score});
                }
            }
            return out.sort((a, b) => b.score - a.score)
                .map(x => x.u).filter((x, i, arr) => arr.indexOf(x) === i);
        }
        """
    )


def click_download(page: Page) -> None:
    selectors = [
        "a[title*='Descargar']", "button[title*='Descargar']", "[aria-label*='Descargar']",
        "a:has-text('Descargar')", "button:has-text('Descargar')", "a:has(i.fa-download)",
        "button:has(i.fa-download)", "a:has(.fa-download)", "button:has(.fa-download)",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 12)):
                item = locator.nth(index)
                if item.is_visible(timeout=1000):
                    item.scroll_into_view_if_needed(timeout=5000)
                    item.click(timeout=5000, force=True)
                    page.wait_for_timeout(1000)
                    return
        except Exception:
            continue
    raise RuntimeError("No se encontró el botón de descarga de Ley Chile.")


def click_sin_firma(page: Page, out_path: Path) -> bool:
    responses: list[Any] = []
    downloads: list[Any] = []

    def on_response(response: Any) -> None:
        content_type = (response.headers.get("content-type") or "").lower()
        url = response.url.lower()
        if "pdf" in content_type or "exportar" in url or "descargar" in url:
            responses.append(response)

    def on_download(download: Any) -> None:
        downloads.append(download)

    page.context.on("response", on_response)
    page.on("download", on_download)
    try:
        selectors = [
            "button:has-text('SIN FIRMA')", "a:has-text('SIN FIRMA')",
            "[role='button']:has-text('SIN FIRMA')", "input[value*='SIN FIRMA']",
            "input[value*='Sin firma']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    locator.click(timeout=10000, force=True)
                    break
            except Exception:
                continue
        else:
            return False

        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            if downloads:
                downloads[-1].save_as(str(out_path))
                assert_pdf(out_path)
                return True
            for response in reversed(responses):
                try:
                    body = response.body()
                    if body.startswith(b"%PDF-"):
                        out_path.write_bytes(body)
                        assert_pdf(out_path)
                        return True
                except Exception:
                    continue
            page.wait_for_timeout(1000)
        return False
    finally:
        try:
            page.context.remove_listener("response", on_response)
            page.remove_listener("download", on_download)
        except Exception:
            pass


def _download_export_link(session: requests.Session, href: str, out_path: Path) -> bool:
    response = session.get(href, timeout=180, allow_redirects=True)
    response.raise_for_status()
    content_type = (response.headers.get("content-type") or "").lower()
    if response.content.startswith(b"%PDF-") or "pdf" in content_type:
        out_path.write_bytes(response.content)
        assert_pdf(out_path)
        return True
    return False


def download_bcn(
    context: BrowserContext,
    source: dict[str, Any],
    out_path: Path,
    debug_directory: Path,
    attempt: int,
    logger: Callable[[str], None],
) -> None:
    url = source.get("url") or f"https://www.bcn.cl/leychile/navegar?idNorma={source['id_norma']}"
    out_path.unlink(missing_ok=True)
    page = context.new_page()
    session: requests.Session | None = None
    try:
        logger(f"  Abriendo Ley Chile: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=180000)
        page.wait_for_timeout(1500)

        session = session_from_context(context, url)
        for href in find_export_links(page):
            logger(f"  Descargando enlace real expuesto por Ley Chile: {href}")
            if _download_export_link(session, href, out_path):
                return

        logger("  Buscando descarga mediante modal de Ley Chile...")
        click_download(page)
        page.wait_for_timeout(1000)
        session.close()
        session = session_from_context(context, url)

        for href in find_export_links(page):
            if _download_export_link(session, href, out_path):
                return

        if click_sin_firma(page, out_path):
            return

        raise RuntimeError("No se pudo obtener el PDF desde Ley Chile.")
    except Exception:
        save_debug(page, debug_directory, f"bcn_intento_{attempt}", out_path.stem)
        raise
    finally:
        if session is not None:
            session.close()
        page.close()


def copy_outline(
    reader: PdfReader,
    writer: PdfWriter,
    outline: list[Any],
    parent: Any,
    page_offset: int,
    page_count: int,
) -> int:
    copied = 0
    last_added = None
    for item in outline:
        if isinstance(item, list):
            if last_added is not None:
                copied += copy_outline(reader, writer, item, last_added, page_offset, page_count)
            continue
        try:
            title = str(getattr(item, "title", None) or item.get("/Title", "")).strip()[:300]
            page_index = reader.get_destination_page_number(item)
        except Exception:
            last_added = None
            continue
        if page_index is None or page_index < 0 or page_index >= page_count:
            last_added = None
            continue
        try:
            last_added = writer.add_outline_item(title or "Marcador", page_offset + page_index, parent=parent)
            copied += 1
        except Exception:
            last_added = None
    return copied


def merge(parts: list[tuple[str, Path]], out_path: Path, title: str, logger: Callable[[str], None]) -> None:
    logger("\nUniendo documentos y creando marcadores...")
    writer = PdfWriter()
    for marker, path in parts:
        reader = PdfReader(str(path))
        start_page = len(writer.pages)
        page_count = len(reader.pages)
        for page in reader.pages:
            writer.add_page(page)
        parent = writer.add_outline_item(marker, start_page)
        try:
            copied = copy_outline(reader, writer, reader.outline or [], parent, start_page, page_count)
            logger(f"  Marcador principal: {marker}; marcadores internos: {copied}")
        except Exception:
            logger(f"  Marcador principal: {marker}; marcadores internos omitidos por incompatibilidad")
    writer.add_metadata({"/Title": title, "/Creator": "GeneradorCompendiosLeyChile"})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        writer.write(handle)
    assert_pdf(out_path)


class CompendiumGenerator:
    def __init__(self, base_directory: Path, config: dict[str, Any], output_path: Path | None = None) -> None:
        self.base_directory = base_directory.resolve()
        self.config = config
        self.work_directory = self.base_directory / "trabajo"
        self.download_directory = self.work_directory / "descargas"
        self.log_path = self.work_directory / "ultimo_log.txt"
        self.output_path = output_path or self.base_directory / output_name_from_base(str(config["salida_base"]))
        if not self.output_path.is_absolute():
            self.output_path = self.base_directory / self.output_path

        download = config.get("descarga", {})
        self.attempts = int(download.get("reintentos_por_norma", 3))
        self.restart_every = int(download.get("reiniciar_contexto_cada", 6))
        self.base_wait = float(download.get("espera_base_segundos", 4))

    def log(self, message: str) -> None:
        print(message, flush=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def _new_context(self, browser: Browser) -> BrowserContext:
        return browser.new_context(
            accept_downloads=True,
            viewport={"width": 1440, "height": 1400},
            user_agent=HEADERS["User-Agent"],
            locale="es-CL",
        )

    def run(self) -> Path:
        self.download_directory.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        self.log(f"Salida: {self.output_path.resolve()}")

        parts: list[tuple[str, Path]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=not bool(self.config.get("bcn_navegador_visible", False))
            )
            context = self._new_context(browser)
            completed_in_context = 0
            try:
                for index, source in enumerate(self.config["fuentes"], start=1):
                    if completed_in_context >= self.restart_every:
                        self.log("  Reiniciando contexto Chromium de forma preventiva...")
                        context.close()
                        context = self._new_context(browser)
                        completed_in_context = 0

                    marker = source["marcador"]
                    path = self.download_directory / f"{source.get('archivo') or safe_name(marker)}.pdf"
                    self.log(f"\n[{index}/{len(self.config['fuentes'])}] {marker}")

                    for attempt in range(1, self.attempts + 1):
                        try:
                            self.log(f"  Intento {attempt}/{self.attempts}")
                            download_bcn(context, source, path, self.download_directory, attempt, self.log)
                            parts.append((marker, path))
                            completed_in_context += 1
                            break
                        except Exception as error:
                            self.log(f"  Falló el intento {attempt}: {type(error).__name__}: {error}")
                            if attempt >= self.attempts:
                                raise RuntimeError(
                                    f"Se agotaron los reintentos para idNorma={source['id_norma']} ({marker})."
                                ) from error
                            context.close()
                            context = self._new_context(browser)
                            completed_in_context = 0
                            delay = min(30.0, self.base_wait * (2 ** (attempt - 1)) + random.uniform(0, 1.5))
                            self.log(f"  Nuevo contexto; reintentando en {delay:.1f} segundos...")
                            time.sleep(delay)
            finally:
                context.close()
                browser.close()

        merge(parts, self.output_path, str(self.config["titulo_compendio"]), self.log)
        self.log(f"\nListo: {self.output_path.resolve()}")
        return self.output_path
