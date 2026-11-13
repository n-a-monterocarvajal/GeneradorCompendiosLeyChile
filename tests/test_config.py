import unittest

from generador_compendios_leychile.config import safe_name, source_url, validate_config


def valid_config():
    return {
        "titulo_compendio": "Compendio de prueba",
        "salida_base": "CompendioPrueba",
        "fuentes": [{
            "tipo": "bcn",
            "id_norma": "1984",
            "url": "https://www.bcn.cl/leychile/navegar?idNorma=1984",
            "archivo": "codigo_penal",
            "marcador": "Código Penal",
        }],
    }


class ConfigTests(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual("Codigo_Penal", safe_name("Código Penal"))

    def test_valid_config(self):
        validate_config(valid_config())

    def test_duplicate_ids_are_rejected(self):
        config = valid_config()
        config["fuentes"].append(dict(config["fuentes"][0], archivo="otro"))
        with self.assertRaisesRegex(ValueError, "duplicado"):
            validate_config(config)

    def test_url_must_match_id(self):
        config = valid_config()
        config["fuentes"][0]["url"] = "https://www.bcn.cl/leychile/navegar?idNorma=1"
        with self.assertRaisesRegex(ValueError, "no coincide"):
            validate_config(config)

    def test_unsafe_filename_is_rejected(self):
        config = valid_config()
        config["fuentes"][0]["archivo"] = "../codigo"
        with self.assertRaisesRegex(ValueError, "inseguro"):
            validate_config(config)

    def test_part_is_valid_and_builds_its_url(self):
        config = valid_config()
        source = config["fuentes"][0]
        del source["url"]
        source["id_parte"] = "8720755"
        validate_config(config)
        self.assertEqual(
            "https://www.bcn.cl/leychile/navegar?idNorma=1984&idParte=8720755", source_url(source)
        )

    def test_part_must_be_numeric(self):
        config = valid_config()
        config["fuentes"][0]["id_parte"] = "art8"
        with self.assertRaisesRegex(ValueError, "id_parte inválido"):
            validate_config(config)

    def test_url_must_match_part(self):
        config = valid_config()
        config["fuentes"][0]["id_parte"] = "8720755"
        config["fuentes"][0]["url"] = "https://www.bcn.cl/leychile/navegar?idNorma=1984&idParte=1"
        with self.assertRaisesRegex(ValueError, "id_parte=8720755"):
            validate_config(config)

    def _with_chapters(self, *chapters):
        config = valid_config()
        base = config["fuentes"][0]
        config["fuentes"] = [
            dict(base, id_norma=str(1000 + i), url=f"https://www.bcn.cl/leychile/navegar?idNorma={1000 + i}",
                 archivo=f"norma_{i}", capitulo=chapter)
            for i, chapter in enumerate(chapters)
        ]
        return config

    def test_chapters_are_valid_when_contiguous(self):
        validate_config(self._with_chapters("I. Constitución", "I. Constitución", "II. Municipios"))

    def test_chapter_must_not_reappear(self):
        with self.assertRaisesRegex(ValueError, "reaparece"):
            validate_config(self._with_chapters("I", "II", "I"))

    def test_chapter_must_be_text(self):
        with self.assertRaisesRegex(ValueError, "capitulo inválido"):
            validate_config(self._with_chapters("I", " "))

    def test_chapter_is_all_or_nothing(self):
        config = self._with_chapters("I", "II")
        del config["fuentes"][1]["capitulo"]
        with self.assertRaisesRegex(ValueError, "todas deben declararlo"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
