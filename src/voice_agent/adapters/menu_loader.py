"""Menu loading adapter."""
import json
from pathlib import Path
from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    sku: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    category: str
    prices: dict[str, float]
    default_variant: str
    allowed_modifiers: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    description: str = ""
    available: bool = True


class MenuCatalog:
    def __init__(self, menu_path: str | Path = "data/menu.json"):
        raw = json.loads(Path(menu_path).read_text(encoding="utf-8"))
        self.restaurant_name: str = raw["restaurant_name"]
        self.currency: str = raw.get("currency", "INR")
        self.store_hours: str = raw.get("store_hours", "")
        self.items: dict[str, MenuItem] = {
            item["sku"]: MenuItem(**item)
            for item in raw.get("items", [])
            if item.get("available", True)
        }

    def search(self, query: str) -> list[MenuItem]:
        q = query.lower().strip()
        if not q or q in {"all", "menu", "everything"}:
            return list(self.items.values())

        tokens = [t for t in q.split() if len(t) > 1]
        scored: list[tuple[int, MenuItem]] = []
        for item in self.items.values():
            haystack = " ".join([item.name, item.category, item.description, *item.aliases]).lower()
            score = sum(1 for tok in tokens if tok in haystack)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored]

    def get_stt_keywords(self) -> list[str]:
        terms = {item.name for item in self.items.values()}
        for item in self.items.values():
            terms.update(item.aliases)
        return sorted(terms)