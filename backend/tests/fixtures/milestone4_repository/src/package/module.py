"""Unicode module documentation: luminance ✨."""

import os as operating_system
from ..helpers import value as imported_value

MODULE_VALUE = 7


def duplicate() -> str:
    """Return the module-scoped value."""
    return "module"


@first_decorator
@second_decorator(argument="value")
async def fetch_value(
    positional: int,
    /,
    optional: str = "default",
    *,
    required: bool,
    **metadata: object,
) -> str:
    """Fetch a value without executing anything."""

    def duplicate() -> str:
        return optional

    return duplicate()


class Container:
    """Container documentation."""

    class Nested:
        pass

    def duplicate(self, item: str) -> str:
        return item

    async def stream(self, *items: str) -> None:
        for item in items:
            operating_system.fspath(item)
