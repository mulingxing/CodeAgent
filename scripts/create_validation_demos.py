"""Create three small, independent Git repositories for validation-mode demos.

Run this script explicitly. Existing repositories are never overwritten.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


REPOSITORIES: dict[str, dict[str, str]] = {
    "demo_algorithm": {
        "src/algokit/__init__.py": "",
        "src/algokit/search.py": (
            "def binary_search(values: list[int], target: int) -> int:\n"
            "    left, right = 0, len(values) - 1\n"
            "    while left < right:\n"
            "        middle = (left + right) // 2\n"
            "        if values[middle] == target:\n"
            "            return middle\n"
            "        if values[middle] < target:\n"
            "            left = middle + 1\n"
            "        else:\n"
            "            right = middle - 1\n"
            "    return -1\n"
        ),
        "tests/test_search.py": (
            "from algokit.search import binary_search\n\n"
            "def test_one_element():\n"
            "    assert binary_search([7], 7) == 0\n\n"
            "def test_last_element():\n"
            "    assert binary_search([2, 4, 7], 7) == 2\n\n"
            "def test_absent_element():\n"
            "    assert binary_search([2, 4, 7], 5) == -1\n"
        ),
    },
    "demo_numeric": {
        "src/simulate.py": (
            "def distance(velocity: float, dt: float, steps: int) -> float:\n"
            "    position = 0.0\n"
            "    for step in range(steps):\n"
            "        position += velocity * (step + 1)\n"
            "    return position\n\n"
            "if __name__ == '__main__':\n"
            "    print(f'distance={distance(2.5, 0.2, 10):.2f}')\n"
        ),
    },
    "demo_plot_svg": {
        "src/plot_svg.py": (
            "def render_svg(levels: list[float]) -> str:\n"
            "    width = height = 80\n"
            "    step = width // (len(levels) - 1)\n"
            "    points = ' '.join(\n"
            "        f'{index * step},{height + round(level * height)}'\n"
            "        for index, level in enumerate(levels)\n"
            "    )\n"
            "    return (\n"
            "        '<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"80\" height=\"80\">'\n"
            "        f'<polyline points=\"{points}\" fill=\"none\" stroke=\"teal\" />'\n"
            "        '</svg>'\n"
            "    )\n\n"
            "if __name__ == '__main__':\n"
            "    print(render_svg([0.0, 0.5, 1.0]))\n"
        ),
    },
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def create_repositories(destination: Path) -> list[Path]:
    destination = destination.resolve()
    planned = [destination / name for name in REPOSITORIES]
    existing = [path for path in planned if path.exists() or path.is_symlink()]
    if existing:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"Existing demo repository; no files were changed: {names}")

    destination.mkdir(parents=True, exist_ok=True)
    for name, files in REPOSITORIES.items():
        repo = destination / name
        repo.mkdir()
        (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(repo, "init", "-b", "main")
        git(repo, "add", ".")
        git(repo, "-c", "user.name=CodeAgent Demo", "-c", "user.email=demo@local.invalid",
            "commit", "-m", f"Demo bug: {name}")
    return planned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "local_repos",
                        help="Parent folder for the three new Git repositories.")
    args = parser.parse_args()
    try:
        created = create_repositories(args.output_dir)
    except FileExistsError as exc:
        parser.exit(1, f"{exc}\n")
    for repo in created:
        print(repo)


if __name__ == "__main__":
    main()
