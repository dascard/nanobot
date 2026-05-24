from core.text_style import normalize_chat_reply_style


def test_keeps_question_mark():
    assert normalize_chat_reply_style("你在哪？") == "你在哪?"


def test_keeps_question_exclaim():
    assert normalize_chat_reply_style("真的假的?!") == "真的假的?!"


def test_keeps_exclaim_question():
    assert normalize_chat_reply_style("不是吧!?") == "不是吧!?"


def test_fullwidth_question_exclaim():
    assert normalize_chat_reply_style("真的吗？！") == "真的吗?!"


def test_punctuation_to_newline():
    assert normalize_chat_reply_style("先看日志，别急。") == "先看日志\n别急"


def test_skip_codeblock():
    text = "```python\nprint('hi')\n```"
    assert normalize_chat_reply_style(text) == text


def test_skip_url():
    text = "看这个 https://example.com/a:b"
    assert normalize_chat_reply_style(text) == text


def test_skip_json():
    text = '{"key": "value"}'
    assert normalize_chat_reply_style(text) == text


def test_skip_html_document():
    html = (
        '<!DOCTYPE html><html><head><style>'
        ':root{--bg:#fff}.card{font-size:14px;color:#123;}'
        '</style></head><body class="news-brief">AI 日报：保持样式。</body></html>'
    )
    assert normalize_chat_reply_style(html) == html
