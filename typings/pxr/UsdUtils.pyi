# usd-core ships no type information; every attribute is Any for pyright.
from typing import Any

def __getattr__(name: str) -> Any: ...
