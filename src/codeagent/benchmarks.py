"""Small, deterministic repair cases used to measure the controlled Agent.

Cases are evaluation inputs, not a list of tasks the API is limited to solving in
principle. This stage intentionally keeps execution to curated single functions.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    title: str
    description: str
    args: tuple[str, ...]
    broken: str
    examples: tuple[tuple[tuple[int, ...], int | bool], ...]


CASES = (
    BenchmarkCase("adult_boundary", "成年边界", "18 岁应视为成年人。", ("age",), "age > 18", (((17,), False), ((18,), True), ((19,), True))),
    BenchmarkCase("even_number", "偶数判断", "判断整数是否为偶数。", ("n",), "n % 2 == 1", (((2,), True), ((3,), False), ((0,), True))),
    BenchmarkCase("pass_score", "及格分数", "分数达到 60 即及格。", ("score",), "score > 60", (((59,), False), ((60,), True), ((61,), True))),
    BenchmarkCase("free_shipping", "包邮门槛", "订单金额达到 99 即包邮。", ("total",), "total > 99", (((98,), False), ((99,), True), ((100,), True))),
    BenchmarkCase("maximum_two", "两个数取大", "返回两个整数中的较大值。", ("a", "b"), "a if a < b else b", (((2, 5), 5), ((7, 3), 7), ((4, 4), 4))),
    BenchmarkCase("minimum_two", "两个数取小", "返回两个整数中的较小值。", ("a", "b"), "a if a > b else b", (((2, 5), 2), ((7, 3), 3), ((4, 4), 4))),
    BenchmarkCase("absolute_value", "绝对值", "返回整数的绝对值。", ("n",), "n if n >= 0 else n", (((-4,), 4), ((0,), 0), ((7,), 7))),
    BenchmarkCase("nonnegative", "负数归零", "负数返回 0，其他整数原样返回。", ("n",), "n if n < 0 else 0", (((-5,), 0), ((0,), 0), ((8,), 8))),
    BenchmarkCase("multiple_of_three", "三的倍数", "判断整数是否为 3 的倍数。", ("n",), "n % 3 != 0", (((3,), True), ((4,), False), ((0,), True))),
    BenchmarkCase("both_divisible", "同时整除", "判断整数是否同时能被 3 和 5 整除。", ("n",), "n % 3 == 0 or n % 5 == 0", (((15,), True), ((9,), False), ((10,), False))),
    BenchmarkCase("square", "平方计算", "返回整数的平方。", ("n",), "n + n", (((3,), 9), ((-4,), 16), ((0,), 0))),
    BenchmarkCase("remaining_stock", "剩余库存", "库存减去售出数量，最低为 0。", ("stock", "sold"), "stock - sold", (((10, 3), 7), ((3, 5), 0), ((0, 0), 0))),
)

CASE_BY_ID = {case.case_id: case for case in CASES}


def render_case(case: BenchmarkCase) -> tuple[str, str]:
    params = ", ".join(case.args)
    source = f"def solve({params}):\n    return {case.broken}\n"
    examples = ",\n    ".join(repr(example) for example in case.examples)
    tests = (
        "import pytest\n"
        "from solution import solve\n\n"
        f"@pytest.mark.parametrize('args, expected', [\n    {examples}\n])\n"
        "def test_expected_behavior(args, expected):\n"
        "    assert solve(*args) == expected\n"
    )
    return source, tests
