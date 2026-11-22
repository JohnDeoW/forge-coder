"""把统计结果格式化为文字报告。"""


def format_report(stats: dict, title: str = "文本统计") -> str:
    lines = [f"== {title} =="]
    lines.append(f"字符数：{stats['chars']}（不含空白 {stats['chars_no_spaces']}）")
    lines.append(f"词数：{stats['words']}，句子数：{stats['sentences']}")
    lines.append(f"平均词长：{stats['avg_word_length']}，平均每句词数：{stats['avg_sentence_words']}")
    if stats["top_words"]:
        top = "、".join(f"{w}({n})" for w, n in stats["top_words"])
        lines.append(f"高频词：{top}")
    lines.append(f"预计阅读：{stats['reading_minutes']} 分钟")
    return "\n".join(lines)
