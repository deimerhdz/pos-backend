"""Tests de la nueva funcionalidad — spec 083-presentaciones-y-promociones,
US1: CRUD del catálogo global de presentaciones (FR-001/FR-002/FR-003),
ejercitado llamando directamente a los handlers del router (mismo patrón que
`test_category_display_order.py`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_presentations_service -v
"""
import unittest
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import fixtures as fx
from app.api.v1.presentations.router import (
    list_presentations,
    get_presentation,
    create_presentation,
    update_presentation,
)
from app.api.v1.presentations.schemas import PresentationCreate, PresentationUpdate


class CreatePresentationTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def test_nace_activa(self):
        p = create_presentation(PresentationCreate(name="Pequeño"), self.db, None)
        self.assertEqual(p.name, "Pequeño")
        self.assertTrue(p.active)

    def test_nombre_duplicado_lanza_409(self):
        create_presentation(PresentationCreate(name="Pequeño"), self.db, None)
        with self.assertRaises(HTTPException) as ctx:
            create_presentation(PresentationCreate(name="Pequeño"), self.db, None)
        self.assertEqual(ctx.exception.status_code, 409)


class ListPresentationsTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def test_lista_ordenada_por_nombre(self):
        fx.make_presentation(self.db, name="Grande")
        fx.make_presentation(self.db, name="Mediano")
        fx.make_presentation(self.db, name="Pequeño")
        self.db.commit()

        page = list_presentations(
            page=1, size=20, active=None, search=None, db=self.db, _=None, user=None,
        )
        self.assertEqual(
            [p.name for p in page["items"]], ["Grande", "Mediano", "Pequeño"]
        )

    def test_filtro_por_active(self):
        fx.make_presentation(self.db, name="Activa", active=True)
        fx.make_presentation(self.db, name="Inactiva", active=False)
        self.db.commit()

        page = list_presentations(
            page=1, size=20, active=True, search=None, db=self.db, _=None, user=None,
        )
        self.assertEqual([p.name for p in page["items"]], ["Activa"])

    def test_busqueda_por_nombre(self):
        fx.make_presentation(self.db, name="Pequeño")
        fx.make_presentation(self.db, name="Grande")
        self.db.commit()

        page = list_presentations(
            page=1, size=20, active=None, search="peque", db=self.db, _=None, user=None,
        )
        self.assertEqual([p.name for p in page["items"]], ["Pequeño"])


class GetPresentationTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def test_devuelve_la_presentacion(self):
        p = fx.make_presentation(self.db, name="Pequeño")
        self.db.commit()
        found = get_presentation(p.id, self.db, None)
        self.assertEqual(found.id, p.id)

    def test_404_si_no_existe(self):
        with self.assertRaises(HTTPException) as ctx:
            get_presentation(uuid4(), self.db, None)
        self.assertEqual(ctx.exception.status_code, 404)


class UpdatePresentationTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def test_renombra(self):
        p = fx.make_presentation(self.db, name="Pequeño")
        self.db.commit()
        updated = update_presentation(p.id, PresentationUpdate(name="Chico"), self.db, None)
        self.assertEqual(updated.name, "Chico")

    def test_renombrar_a_nombre_duplicado_lanza_409(self):
        fx.make_presentation(self.db, name="Pequeño")
        otra = fx.make_presentation(self.db, name="Mediano")
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            update_presentation(otra.id, PresentationUpdate(name="Pequeño"), self.db, None)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_renombrar_al_mismo_nombre_no_lanza_409(self):
        p = fx.make_presentation(self.db, name="Pequeño")
        self.db.commit()
        updated = update_presentation(p.id, PresentationUpdate(name="Pequeño"), self.db, None)
        self.assertEqual(updated.name, "Pequeño")

    def test_desactivar(self):
        p = fx.make_presentation(self.db, name="Pequeño", active=True)
        self.db.commit()
        updated = update_presentation(p.id, PresentationUpdate(active=False), self.db, None)
        self.assertFalse(updated.active)

    def test_reactivar(self):
        """US1 Escenario 5: `active` se puede cambiar en ambos sentidos."""
        p = fx.make_presentation(self.db, name="Pequeño", active=False)
        self.db.commit()
        updated = update_presentation(p.id, PresentationUpdate(active=True), self.db, None)
        self.assertTrue(updated.active)

    def test_404_si_no_existe(self):
        with self.assertRaises(HTTPException) as ctx:
            update_presentation(uuid4(), PresentationUpdate(name="X"), self.db, None)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
