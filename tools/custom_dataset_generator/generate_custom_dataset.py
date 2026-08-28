#!/usr/bin/env python3
"""generate_custom_dataset —— 自由生成 ais_bench 自定义数据集（mcq / qa）。

依据 docs/source_zh_cn/advanced_tutorials/custom_dataset.md 中定义的格式，
生成 ``.jsonl`` / ``.csv`` 两种格式的自定义数据集：

- 每条 case 的输入（question）字符随机生成；
- 输入字符个数遵循泊松分布（--input-lambda 控制均值）；
- 每条 case 的 ``max_tokens`` 遵循泊松分布（--max-token-lambda 控制均值）；
- 支持选择题（mcq）与问答题（qa）两种数据类型。

用法示例::

    # 生成 1000 条 mcq 数据（4 个选项），输入长度均值 256、max_tokens 均值 512
    python tools/custom_dataset_generator/generate_custom_dataset.py \\
        --output-path test_mcq.jsonl --data-type mcq --num-cases 1000 \\
        --input-lambda 256 --max-token-lambda 512 --option-count 4

    # 生成 2000 条 qa 数据，输出 csv 格式，中文字符输入，固定随机种子保证可复现
    python tools/custom_dataset_generator/generate_custom_dataset.py \\
        --output-path test_qa.csv --data-type qa --num-cases 2000 \\
        --input-lambda 128 --max-token-lambda 1024 --charset chinese --seed 42

    # qa 类型不生成 answer 字段（数据集无正确答案）
    python tools/custom_dataset_generator/generate_custom_dataset.py \\
        --output-path test_qa.jsonl --data-type qa --num-cases 500 \\
        --input-lambda 64 --max-token-lambda 256 --no-answer

依赖：Python3 标准库 + numpy。
"""

import argparse
import csv
import json
import os
import string
import sys

import numpy as np

PRESET_CHARSETS = ('ascii', 'alnum', 'letter', 'digit', 'chinese')
ALNUM_CHARSET = string.ascii_letters + string.digits


def build_charset(name):
    """根据名称或自定义字符串构造 question 随机字符集。

    Args:
        name: 预置字符集名称（ascii/alnum/letter/digit/chinese），
            或直接传入任意自定义字符串作为字符集。

    Returns:
        str: 用于随机取字符的字符集。
    """
    if name == 'ascii':
        return string.ascii_letters + string.digits + string.punctuation + ' '
    if name == 'alnum':
        return ALNUM_CHARSET
    if name == 'letter':
        return string.ascii_letters
    if name == 'digit':
        return string.digits
    if name == 'chinese':
        # 常用 CJK 统一表意文字区（\u4e00-\u9fff）
        return ''.join(chr(code) for code in range(0x4E00, 0x9FFF + 1))
    if not name:
        raise ValueError('charset 不能为空字符串')
    return name


def poisson_clamped(rng, lam, min_val, max_val):
    """按泊松分布采样一个整数并截断到 [min_val, max_val]。"""
    value = int(rng.poisson(lam))
    return min(max(value, min_val), max_val)


def random_text(rng, length, charset):
    """从 charset 中随机抽取 length 个字符组成字符串。"""
    if length <= 0:
        return ''
    idxs = rng.integers(0, len(charset), size=length)
    return ''.join(charset[i] for i in idxs)


def make_case(rng, args, charset, option_charset):
    """生成一条 case（dict），字段顺序与文档样例保持一致。

    mcq: question, A, B, ..., answer, [max_tokens]
    qa : question, [answer], [max_tokens]
    """
    in_len = poisson_clamped(rng, args.input_lambda, args.input_min, args.input_max)
    case = {'question': random_text(rng, in_len, charset)}

    if args.data_type == 'mcq':
        letters = [chr(ord('A') + i) for i in range(args.option_count)]
        for letter in letters:
            opt_len = int(rng.integers(1, args.option_max_len + 1))
            case[letter] = random_text(rng, opt_len, option_charset)
        # answer 必须是上述选项之一（如 A, B, C 等）
        case['answer'] = str(rng.choice(letters))
    elif not args.no_answer:
        ans_len = int(rng.integers(1, args.option_max_len + 1))
        case['answer'] = random_text(rng, ans_len, option_charset)

    if not args.no_max_token:
        case['max_tokens'] = poisson_clamped(
            rng, args.max_token_lambda, args.max_token_min, args.max_token_max)
    return case


def write_dataset(cases, output_path, fmt):
    """将 case 列表写入 .jsonl 或 .csv 文件。"""
    if fmt == 'jsonl':
        with open(output_path, 'w', encoding='utf-8') as f:
            for case in cases:
                f.write(json.dumps(case, ensure_ascii=False) + '\n')
    else:
        header = list(cases[0].keys())
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for case in cases:
                writer.writerow(case)


def print_summary(cases, args):
    """打印生成结果摘要，便于核对泊松分布参数是否符合预期。"""
    in_lens = [len(c['question']) for c in cases]
    max_tokens = [c.get('max_tokens') for c in cases if 'max_tokens' in c]
    print(f'输入长度: mean={sum(in_lens) / len(in_lens):.1f}, '
          f'min={min(in_lens)}, max={max(in_lens)} '
          f'(lambda={args.input_lambda:g}, clamp=[{args.input_min}, {args.input_max}])')
    if max_tokens:
        print(f'max_tokens: mean={sum(max_tokens) / len(max_tokens):.1f}, '
              f'min={min(max_tokens)}, max={max(max_tokens)} '
              f'(lambda={args.max_token_lambda:g}, '
              f'clamp=[{args.max_token_min}, {args.max_token_max}])')
    if args.data_type == 'mcq':
        print(f'选项个数: {args.option_count}（A ~ '
              f'{chr(ord("A") + args.option_count - 1)}）')
    if args.no_answer:
        print('answer 字段: 未生成（qa 数据集无正确答案）')


def parse_args():
    parser = argparse.ArgumentParser(
        description='自由生成 ais_bench 自定义数据集：输入字符随机，'
                    '输入字符个数与 max_tokens 均遵循泊松分布')
    parser.add_argument('--output-path', required=True,
                        help='输出数据集文件路径（.jsonl / .csv），'
                             '默认根据扩展名推断格式')
    parser.add_argument('--format', choices=['jsonl', 'csv'], default=None,
                        help='输出格式；默认根据 --output-path 扩展名推断'
                             '（.jsonl -> jsonl，.csv -> csv）')
    parser.add_argument('--data-type', choices=['mcq', 'qa'], required=True,
                        help='数据类型：mcq 选择题 / qa 问答题')
    parser.add_argument('--num-cases', type=int, default=100,
                        help='生成的 case 条数（默认 100）')
    parser.add_argument('--input-lambda', type=float, default=128,
                        help='输入字符个数泊松分布均值 lambda（默认 128）')
    parser.add_argument('--input-min', type=int, default=1,
                        help='输入字符个数下限，采样结果会截断到该值（默认 1）')
    parser.add_argument('--input-max', type=int, default=None,
                        help='输入字符个数上限，采样结果会截断到该值'
                             '（默认 input_lambda*4 + 10）')
    parser.add_argument('--max-token-lambda', type=float, default=512,
                        help='max_tokens 泊松分布均值 lambda（默认 512）')
    parser.add_argument('--max-token-min', type=int, default=1,
                        help='max_tokens 下限，采样结果会截断到该值（默认 1）')
    parser.add_argument('--max-token-max', type=int, default=None,
                        help='max_tokens 上限，采样结果会截断到该值'
                             '（默认 max_token_lambda*4 + 16）')
    parser.add_argument('--charset', type=str, default='ascii',
                        help='question 随机字符集：预置可选 '
                             'ascii/alnum/letter/digit/chinese，'
                             '或直接传任意自定义字符串作为字符集（默认 ascii）')
    parser.add_argument('--option-count', type=int, default=4,
                        help='mcq 选项个数（默认 4，范围 2~26，'
                             '从 A 开始生成连续的选项字母）')
    parser.add_argument('--option-max-len', type=int, default=16,
                        help='mcq 选项值与 qa 答案的最大随机长度（默认 16，'
                             '实际长度在 [1, option_max_len] 内均匀取值）')
    parser.add_argument('--no-answer', action='store_true',
                        help='qa 类型不生成 answer 字段（数据集无正确答案）')
    parser.add_argument('--no-max-token', action='store_true',
                        help='不生成 max_tokens 字段')
    parser.add_argument('--seed', type=int, default=None,
                        help='随机种子，设置后生成结果可复现')
    return parser.parse_args()


def main():
    args = parse_args()

    # ---- 参数校验 ----
    if args.num_cases < 1:
        sys.exit(f'错误: --num-cases 必须 >= 1，当前为 {args.num_cases}')
    if args.input_lambda <= 0:
        sys.exit(f'错误: --input-lambda 必须为正数，当前为 {args.input_lambda}')
    if args.max_token_lambda <= 0:
        sys.exit(f'错误: --max-token-lambda 必须为正数，'
                 f'当前为 {args.max_token_lambda}')
    if args.input_min < 0:
        sys.exit(f'错误: --input-min 必须 >= 0，当前为 {args.input_min}')
    if args.max_token_min < 1:
        sys.exit(f'错误: --max-token-min 必须 >= 1，当前为 {args.max_token_min}')
    if not (2 <= args.option_count <= 26):
        sys.exit(f'错误: --option-count 必须在 2~26 之间，当前为 {args.option_count}')
    if args.option_max_len < 1:
        sys.exit(f'错误: --option-max-len 必须 >= 1，当前为 {args.option_max_len}')

    if args.input_max is None:
        args.input_max = int(args.input_lambda * 4 + 10)
    if args.max_token_max is None:
        args.max_token_max = int(args.max_token_lambda * 4 + 16)
    if args.input_max < args.input_min:
        sys.exit(f'错误: --input-max({args.input_max}) 必须 >= '
                 f'--input-min({args.input_min})')
    if args.max_token_max < args.max_token_min:
        sys.exit(f'错误: --max-token-max({args.max_token_max}) 必须 >= '
                 f'--max-token-min({args.max_token_min})')

    fmt = args.format or os.path.splitext(args.output_path)[1].lstrip('.').lower()
    if fmt not in ('jsonl', 'csv'):
        sys.exit(f'错误: 无法从 --output-path 推断格式，请显式指定 '
                 f'--format jsonl|csv（当前扩展名: {fmt!r}）')

    charset = build_charset(args.charset)
    option_charset = ALNUM_CHARSET

    rng = np.random.default_rng(args.seed)
    cases = [make_case(rng, args, charset, option_charset)
             for _ in range(args.num_cases)]

    output_dir = os.path.dirname(os.path.abspath(args.output_path))
    os.makedirs(output_dir, exist_ok=True)
    write_dataset(cases, args.output_path, fmt)

    print(f'已生成 {args.num_cases} 条 {args.data_type} 数据: '
          f'{os.path.abspath(args.output_path)}（格式: {fmt}）')
    print_summary(cases, args)
    print(f'随机种子: {args.seed}')


if __name__ == '__main__':
    main()
