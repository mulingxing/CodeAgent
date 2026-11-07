"""Create a disposable two-file Git repair example. Refuses to overwrite a repo."""

from pathlib import Path
import subprocess


root = Path(__file__).resolve().parents[1] / "local_repos" / "demo_shop"
if root.exists():
    raise SystemExit(f"Already exists; leaving it unchanged: {root}")

(root / "src" / "shop").mkdir(parents=True)
(root / "tests").mkdir()
(root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
(root / "src" / "shop" / "__init__.py").write_text("", encoding="utf-8")
(root / "src" / "shop" / "stock.py").write_text(
    "def remaining(stock: int, sold: int, reserved: int) -> int:\n"
    "    return max(0, stock - sold)\n", encoding="utf-8"
)
(root / "src" / "shop" / "checkout.py").write_text(
    "def can_checkout(stock: int, reserved: int, requested: int) -> bool:\n"
    "    return requested > 0 and requested <= stock\n", encoding="utf-8"
)
(root / "tests" / "test_inventory.py").write_text(
    "from shop.stock import remaining\n"
    "from shop.checkout import can_checkout\n\n"
    "def test_reserved_stock_reduces_remaining():\n"
    "    assert remaining(10, 2, 3) == 5\n\n"
    "def test_checkout_respects_reservations():\n"
    "    assert can_checkout(10, 7, 4) is False\n\n"
    "def test_checkout_with_free_stock():\n"
    "    assert can_checkout(10, 7, 3) is True\n", encoding="utf-8"
)
for args in [
    ["init", "-b", "main"], ["add", "."],
    ["-c", "user.name=CodeAgent Demo", "-c", "user.email=demo@local.invalid", "commit", "-m", "Demo with two inventory bugs"],
]:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
print(root)
