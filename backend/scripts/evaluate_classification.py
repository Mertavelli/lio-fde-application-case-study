"""Run the curated classification check against the configured model."""

import json
from collections import Counter
from pathlib import Path

from app.core.config import settings
from app.services.item_classification import classify_items


def main() -> None:
    if not settings.openai_api_key:
        raise SystemExit("Set OPENAI_API_KEY before evaluating")
    cases = json.loads((Path(__file__).parents[2] / "fixtures/classification_cases.json").read_text())
    expected = [kind for _, kind in cases]
    actual = []
    for start in range(0, len(cases), 10):
        actual.extend(item["kind"] for item in classify_items([description for description, _ in cases[start:start + 10]]))
    misses = [(description, wanted, got) for (description, wanted), got in zip(cases, actual) if wanted != got]
    print(f"Accuracy: {len(cases) - len(misses)}/{len(cases)}")
    print("Expected:", dict(Counter(expected)))
    for description, wanted, got in misses:
        print(f"Mismatch: {description}: expected {wanted}, got {got}")
    critical = [(wanted, got) for _, wanted, got in misses if wanted not in ("other", "uncertain") or got not in ("other", "uncertain")]
    if len(misses) > 2 or critical:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
