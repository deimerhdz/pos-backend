"""Verificación de no-regresión — FR-004 (spec 037): `POST /users` (creación
con contraseña directa) desaparece por completo, sin vía alterna
(contracts/users-create-removed.md). Solo `GET /users`,
`PATCH /users/{id}/role` y `PATCH /users/{id}/status` deben seguir
registrados en el router.

    python -m unittest app.characterization_tests.test_users_create_removed -v
"""
import unittest

from app.api.v1.users import router as users_router_module
from app.api.v1.users import schemas as users_schemas_module


class UsersCreateRemovedTests(unittest.TestCase):
    def test_router_no_tiene_ninguna_ruta_post_en_la_raiz(self):
        post_root_routes = [
            route for route in users_router_module.router.routes
            if route.path == "/users" and "POST" in route.methods
        ]
        self.assertEqual(post_root_routes, [])

    def test_create_user_ya_no_existe_como_handler(self):
        self.assertFalse(hasattr(users_router_module, "create_user"))

    def test_usercreate_ya_no_existe_como_schema(self):
        self.assertFalse(hasattr(users_schemas_module, "UserCreate"))


if __name__ == "__main__":
    unittest.main()
