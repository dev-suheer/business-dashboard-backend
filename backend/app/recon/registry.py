"""Category registry (plan.md section 11).

Ten more categories follow this one. Registering their adapter here is all that
should be needed — the models, the matcher and the export stay generic.
"""

from typing import Any

from app.recon.categories.base import CategoryAdapter

_ADAPTERS: dict[str, CategoryAdapter[Any]] = {}


class UnknownCategoryError(KeyError):
    """No adapter is registered under that category name."""


def register(adapter: CategoryAdapter[Any]) -> CategoryAdapter[Any]:
    _ADAPTERS[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> CategoryAdapter[Any]:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise UnknownCategoryError(
            f"Unknown category {name!r}. Available: {', '.join(categories())}"
        )


def categories() -> list[str]:
    return sorted(_ADAPTERS)


def _register_builtins() -> None:
    # Imported here rather than at module scope to keep the import graph acyclic:
    # a category imports the registry's types, not the registry itself.
    from app.recon.categories.eshop import adapter as eshop

    register(eshop.ADAPTER)


_register_builtins()
