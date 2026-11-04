import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from generador_compendios_leychile.generador import merge


def blank_pdf(path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def outline_tree(reader: PdfReader, items=None):
    items = reader.outline if items is None else items
    tree = []
    for item in items:
        if isinstance(item, list):
            tree[-1] = (tree[-1][0], tree[-1][1], outline_tree(reader, item))
        else:
            tree.append((item.title, reader.get_destination_page_number(item), []))
    return tree


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.a = blank_pdf(self.dir / "a.pdf", 2)
        self.b = blank_pdf(self.dir / "b.pdf", 3)
        self.c = blank_pdf(self.dir / "c.pdf", 1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_flat_outline_without_chapters(self):
        out = self.dir / "plano.pdf"
        merge([("A", self.a), ("B", self.b)], out, "Prueba", lambda _: None)
        reader = PdfReader(str(out))
        self.assertEqual([("A", 0, []), ("B", 2, [])], outline_tree(reader))

    def test_chapters_group_consecutive_norms(self):
        out = self.dir / "capitulos.pdf"
        merge([("A", self.a, "I"), ("B", self.b, "I"), ("C", self.c, "II")], out, "Prueba", lambda _: None)
        reader = PdfReader(str(out))
        self.assertEqual(6, len(reader.pages))
        self.assertEqual(
            [("I", 0, [("A", 0, []), ("B", 2, [])]), ("II", 5, [("C", 5, [])])],
            outline_tree(reader),
        )


if __name__ == "__main__":
    unittest.main()
