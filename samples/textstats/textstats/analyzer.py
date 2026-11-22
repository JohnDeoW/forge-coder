"""文本统计。"""

WORDS_PER_MINUTE = 200
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that", "for", "on", "with", "as", "was"}


def analyze(text, top_n=3):
    if top_n < 1:
        raise ValueError("top_n 必须为正整数")
    result = {}
    result["chars"] = len(text)
    result["chars_no_spaces"] = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

    # 分词
    words = []
    current = ""
    for ch in text:
        if ch.isalpha() or ch == "'":
            current += ch.lower()
        else:
            if current:
                words.append(current.strip("'"))
                current = ""
    if current:
        words.append(current.strip("'"))
    words = [w for w in words if w]
    result["words"] = len(words)

    # 句子
    sentences = 0
    buf = ""
    for ch in text:
        buf += ch
        if ch in ".!?":
            if buf.strip(" .!?\n"):
                sentences += 1
            buf = ""
    if buf.strip(" .!?\n"):
        sentences += 1
    result["sentences"] = sentences

    if len(words) > 0:
        total = 0
        for w in words:
            total += len(w)
        result["avg_word_length"] = round(total / len(words), 2)
    else:
        result["avg_word_length"] = 0.0

    if sentences > 0:
        result["avg_sentence_words"] = round(len(words) / sentences, 2)
    else:
        result["avg_sentence_words"] = 0.0

    # 高频词（排除停用词，次数相同时按字母顺序）
    counts = {}
    for w in words:
        if w in STOPWORDS:
            continue
        if w in counts:
            counts[w] += 1
        else:
            counts[w] = 1
    items = list(counts.items())
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    result["top_words"] = items[:top_n]

    minutes = len(words) / WORDS_PER_MINUTE
    if minutes < 1 and len(words) > 0:
        minutes = 1
    result["reading_minutes"] = round(minutes, 1)
    return result


def compare(text_a, text_b):
    """对比两段文本的用词：共同词、各自独有的词与相似度（Jaccard）。"""
    words_a = []
    current = ""
    for ch in text_a:
        if ch.isalpha() or ch == "'":
            current += ch
        else:
            if current:
                words_a.append(current.strip("'"))
                current = ""
    if current:
        words_a.append(current.strip("'"))

    words_b = []
    current = ""
    for ch in text_b:
        if ch.isalpha() or ch == "'":
            current += ch
        else:
            if current:
                words_b.append(current.strip("'"))
                current = ""
    if current:
        words_b.append(current.strip("'"))

    set_a = set(w for w in words_a if w and w not in STOPWORDS)
    set_b = set(w for w in words_b if w and w not in STOPWORDS)
    common = set_a & set_b
    union = set_a | set_b
    if len(union) > 0:
        similarity = round(len(common) / len(union), 2)
    else:
        similarity = 0.0
    return {
        "common": sorted(common),
        "only_a": sorted(set_a - set_b),
        "only_b": sorted(set_b - set_a),
        "similarity": similarity,
    }
