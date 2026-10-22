"""Typing, dataclasses, enums and protocol usage that must stay silent."""

from __future__ import annotations

import enum
import functools
import itertools
import re
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar, Final, Generic, Literal, NamedTuple, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
MAX_ITEMS: Final = 100


class Row(NamedTuple):
    key: str
    value: int = 0


@dataclass(frozen=True, order=True)
class Point:
    x: float
    y: float = 0.0
    tags: tuple[str, ...] = field(default_factory=tuple)
    registry: ClassVar[dict[str, "Point"]] = {}

    def distance(self, other: "Point") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Kind(enum.IntEnum):
    A = 1
    B = 2


class Code(enum.StrEnum):
    OK = "ok"


@runtime_checkable
class Sized2(Protocol):
    def __len__(self) -> int: ...


class Box(Generic[T]):
    def __init__(self, item: T) -> None:
        self.item = item

    def map(self, function: Callable[[T], V]) -> Box[V]:
        return Box(function(self.item))


def chunked(items: Sequence[T], size: int = 10) -> Iterator[list[T]]:
    iterator = iter(items)
    while batch := list(itertools.islice(iterator, size)):
        yield batch


def index_by(rows: Iterable[Row], *, key: Callable[[Row], K] = lambda row: row.key) -> Mapping[K, list[Row]]:
    grouped: dict[K, list[Row]] = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row)
    return grouped


class Parser:
    """Calls stdlib functions with keywords that exist in the 3.12 stdlib."""

    pattern = re.compile(r"(?P<word>\w+)", flags=re.IGNORECASE | re.UNICODE)

    def parse(self, text: str, *, limit: int = 0) -> list[str]:
        found = self.pattern.findall(string=text, pos=0)
        joined = "".join(map(str, itertools.chain.from_iterable([[1, 2], [3]])))
        numbers = sorted(map(int, re.findall(r"\d+", joined)), reverse=True)
        rendered = re.sub(pattern=r"\d", repl="#", string=joined, count=limit or 0, flags=re.MULTILINE)
        return [*found, rendered, *(str(item) for item in numbers), str(len(numbers))][: MAX_ITEMS or None]


@functools.lru_cache(maxsize=128, typed=True)
def memo(name: str, payload: tuple[int, ...] = ()) -> int:
    return sum(payload) + len(name)


def framed(text: str, *, width: int = 40, fill: str = "-", align: Literal["left", "right"] = "left") -> str:
    body = text if align == "left" else text.rjust(width, fill)
    return f"{body:{fill}^{width}}"


def total(values: Annotated[Iterable[int], "numbers"], *, drop_negatives: bool = True) -> int:
    data = [value for value in values if value >= 0 or not drop_negatives]
    return sum(data, start=0)


def main() -> None:
    parser = Parser()
    points = [Point(1.0, 2.0), Point(3.0)]
    box: Box[int] = Box(1)
    print(
        parser.parse("a1 b2", limit=2),
        chunked(list(range(5)), size=2),
        index_by([Row("a", 1)], key=lambda row: row.key),
        memo("x", (1, 2)),
        framed("hi", width=5),
        total([1, -2, 3], drop_negatives=False),
        box.map(str).item,
        points[0].distance(points[1]),
        typing.get_type_hints(Point),
        Colour.RED.value,
        Kind.A.value,
        Code.OK.value,
        Sized2,
        MAX_ITEMS,
    )


if __name__ == "__main__":
    main()
