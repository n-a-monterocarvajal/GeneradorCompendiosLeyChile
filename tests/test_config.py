import unittest

from generador_compendios_leychile.config import safe_name, validate_config


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


if __name__ == "__main__":
    unittest.main()
