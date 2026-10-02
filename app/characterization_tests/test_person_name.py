"""Reglas del nombre completo (spec 091, A-100): vectores 1–26 de
`specs/091-admin-subdominio-nombre-completo/contracts/full-name-rules.md`.
La suite del frontend (`full-name.validator.spec.ts`) recorre los mismos vectores.

    python -m unittest app.characterization_tests.test_person_name -v
"""
import unittest

from app.core.person_name import (
    FULL_NAME_FORMAT_MESSAGE,
    FULL_NAME_LENGTH_MESSAGE,
    FULL_NAME_REQUIRED_MESSAGE,
    normalize_full_name,
)

REQUIRED = FULL_NAME_REQUIRED_MESSAGE
LENGTH = FULL_NAME_LENGTH_MESSAGE
FORMAT = FULL_NAME_FORMAT_MESSAGE

VALID = [
    ("María Pérez", "María Pérez"),                                    # 1
    ("José Ñañez O'Brien-Díaz", "José Ñañez O'Brien-Díaz"),           # 2
    ("O’Brien", "O’Brien"),                                           # 3
    ("François", "François"),                                         # 4
    ("Zoë", "Zoë"),
    ("Müller", "Müller"),
    ("Søren", "Søren"),
    ("Łukasz", "Łukasz"),
    ("Đorđe", "Đorđe"),
    ("Nguyễn", "Nguyễn"),
    ("Al", "Al"),                                                     # 5
    ("a" * 100, "a" * 100),                                           # 6
    (" Ana ", "Ana"),                                                 # 7
    ("María   Pérez", "María   Pérez"),                               # 8
    ("María", "María"),                                         # 9 (NFD → NFC)
]

INVALID = [
    (None, REQUIRED),                                                 # 10
    ("", REQUIRED),                                                   # 11
    ("     ", REQUIRED),                                              # 12
    ("A", LENGTH),                                                    # 13
    ("-", LENGTH),                                                    # 14
    ("a" * 101, LENGTH),                                              # 15
    ("1" * 101, LENGTH),                                              # 16 (el largo gana)
    ("<script>alert(1)</script>", FORMAT),                            # 17
    ("Ana3", FORMAT),                                                 # 18
    ("Ana@", FORMAT),
    ("Ana.", FORMAT),
    ("Ana,", FORMAT),
    ("Ana 😀", FORMAT),                                               # 19
    ("Иван", FORMAT),                                                 # 20
    ("李雷", FORMAT),
    ("محمد", FORMAT),
    ("---", FORMAT),                                                  # 21
    ("''", FORMAT),                                                   # 22
    ("A-", FORMAT),                                                   # 23
    ("Ana\tPérez", FORMAT),                                           # 24
    ("Ana\nPérez", FORMAT),
    ("A×B", FORMAT),                                                  # 25
    ("😀" * 60, FORMAT),                                              # 26 (puntos de código)
]


class FullNameRulesTests(unittest.TestCase):
    def test_mensajes_literales(self):
        self.assertEqual(REQUIRED, "El nombre es obligatorio")
        self.assertEqual(LENGTH, "El nombre debe tener entre 2 y 100 caracteres")
        self.assertEqual(
            FORMAT,
            "El nombre solo puede contener letras, espacios, apóstrofes y guiones, y al menos dos letras",
        )

    def test_vectores_validos(self):
        for value, expected in VALID:
            with self.subTest(value=value):
                self.assertEqual(normalize_full_name(value), expected)

    def test_vectores_invalidos(self):
        for value, message in INVALID:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    normalize_full_name(value)
                self.assertEqual(str(ctx.exception), message)

    def test_el_valor_guardado_es_nfc(self):
        self.assertEqual(len(normalize_full_name("María")), 5)


if __name__ == "__main__":
    unittest.main()
