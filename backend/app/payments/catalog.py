from dataclasses import dataclass
from types import MappingProxyType

from app.core.errors import ProductNotFound


@dataclass(frozen=True, slots=True)
class Product:
    id: str
    name: str
    description: str
    unit_amount: int
    currency: str


_PRODUCTS: tuple[Product, ...] = (
    Product(
        id="payflow-starter",
        name="PayFlow Starter",
        description="Demo plan - starter tier",
        unit_amount=990,
        currency="eur",
    ),
    Product(
        id="payflow-pro",
        name="PayFlow Pro",
        description="Demo plan - pro tier",
        unit_amount=2990,
        currency="eur",
    ),
    Product(
        id="payflow-report",
        name="Financial Report Pack",
        description="Demo one-off report bundle",
        unit_amount=4900,
        currency="eur",
    ),
)

CATALOG: MappingProxyType[str, Product] = MappingProxyType({p.id: p for p in _PRODUCTS})


def get_product(product_id: str) -> Product:
    try:
        return CATALOG[product_id]
    except KeyError:
        raise ProductNotFound(f"Unknown product '{product_id}'") from None


def list_products() -> list[Product]:
    return list(CATALOG.values())
