"""命令行入口：读取台账，输出 JSON 报告或居民可读文本。

用法：
    python3 -m beachwatch [--data PATH] [--season 暖季B]
                          [--mode per_bank|whole_bank] [--format text|json]

只读 data/records.json，不修改原始资料。所有结论都能按手记编号回查原话。
"""
from __future__ import annotations

import argparse
import json
import sys

from .analysis import build_observations, build_states, compare_all
from .model import load_records
from .parser import parse_all
from .report import build_report, render_text


def analyse(data_path: str | None = None, season: str | None = None,
            mode: str = 'per_bank'):
    records = load_records(data_path) if data_path else load_records()
    parses = parse_all(records)
    observations, bags, unresolved = build_observations(records, parses)
    states = build_states(records, observations, unresolved, parses)
    comparisons = compare_all(records, states)
    seasons = sorted({v.season for v in records.visits})
    if season is None:
        season = seasons[-1]
    if season not in seasons:
        raise SystemExit(f'未知季节 {season}；可选：{", ".join(seasons)}')
    report = build_report(
        records, parses, states, observations, bags, unresolved, comparisons,
        season=season, display_mode=mode,
    )
    return records, parses, observations, bags, unresolved, states, comparisons, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='柳涧净滩台账分析与公开报告')
    parser.add_argument('--data', help='records.json 路径（默认 data/records.json）')
    parser.add_argument('--season', help='报告针对的季节，如 暖季B')
    parser.add_argument('--mode', choices=['per_bank', 'whole_bank'], default='per_bank')
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    args = parser.parse_args(argv)

    report = analyse(args.data, args.season, args.mode)[-1]
    if args.format == 'json':
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
