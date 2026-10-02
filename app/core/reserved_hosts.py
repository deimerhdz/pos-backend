"""Subdominios reservados de la plataforma (spec 091, A-101).

Lista única: ningún negocio puede registrarse con uno de estos hosts. La misma
lista vive en `pos-heladeria` (`environment.reservedSlugs`) con una prueba de
paridad en cada repositorio.
"""

# Host que identifica el ámbito de plataforma (Super Admin): `admin.<dominio>`.
PLATFORM_HOST = "admin"

RESERVED_SUBDOMAINS = frozenset({"www", "app", "admin", "assets", "api", "docs"})


def is_reserved_subdomain(value: str) -> bool:
    return value.strip().lower() in RESERVED_SUBDOMAINS
