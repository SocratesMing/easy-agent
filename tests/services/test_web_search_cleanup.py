"""联网搜索结果的摘要清洗与同事件去重。

这两步是为了解决「回答里的结论与标注的来源链接对不上」：

- 摘要来自整页抓取，常含导航栏、侧栏推荐、版权声明，模型会把这些当成
  新闻事实（例如把「国内新闻精选」里的推荐标题写成正文）；
- 同一条新闻被多家转载时，摘要各截一段、内容互有缺失，模型会把 A 家的
 数字挂到 B 家的链接上。
"""

from __future__ import annotations

from easy_agent.tools.web_search import _clean_summary, _dedupe_items, _format_items

# 新浪财经类站点的导航栏：一长串用空格分隔的栏目名，没有句读标点。
_NAV_BAR = " ".join(
    [
        "交易提示", "操盘必读", "证券报", "最新公告", "限售解禁", "数据中心",
        "条件选股", "券商评级", "股价预测", "板块行情", "千股千评", "个股诊断",
        "大宗交易", "财报查询", "业绩预告", "基金净值", "基金对比", "基金排名",
    ]
)

_CORE = (
    "金融市场运行总体稳定，设立应对非银机构大额赎回的流动性支持工具，"
    "10年期国债收益率运行在1.65%附近，坚持市场在汇率形成中的决定性作用。"
)


def _item(title: str, link: str, summary: str) -> dict:
    return {
        "title": title,
        "link": link,
        "hostname": "news.example",
        "publishedTime": "2026-11-05T00:00:00Z",
        "summary": summary,
    }


# ── 摘要清洗 ──────────────────────────────────────────────────────────


def test_clean_summary_drops_sidebar_news_list():
    """「#### 国内新闻精选」之后的推荐列表不是正文。"""
    raw = (
        "11月5日，第九届中国国际进口博览会暨虹桥国际经济论坛开幕式在上海举行。\n\n"
        "本届展会多项数据再创新高：汇聚162个国家、地区和国际组织，4142家境外企业参展。\n\n"
        "#### 国内新闻精选：\n\n"
        "中国AIMS望远镜发现耀斑爆发期间太阳大气磁场明显减弱现象\n\n"
        "第七届世界媒体峰会将在北京举行"
    )
    cleaned = _clean_summary(raw)

    assert "第七届世界媒体峰会" not in cleaned
    assert "中国AIMS望远镜" not in cleaned
    assert "开幕式在上海举行" in cleaned
    assert "4142家境外企业" in cleaned


def test_clean_summary_drops_nav_paragraph():
    """整段导航栏被剥掉，保留同一条结果里的正文段。"""
    raw = f"{_NAV_BAR}\n\n交通银行亮相第九届进博会，展示跨境融通服务方案。"
    cleaned = _clean_summary(raw)

    assert "操盘必读" not in cleaned
    assert "条件选股" not in cleaned
    assert "交通银行亮相第九届进博会" in cleaned


def test_clean_summary_drops_trailing_boilerplate():
    raw = (
        "中国人民银行行长潘功胜报告2025年10月以来金融工作情况。\n\n"
        "更多精彩内容请进入国内新闻\n\n"
        "【编辑:蔡星雨】"
    )
    cleaned = _clean_summary(raw)

    assert "更多精彩内容" not in cleaned
    assert "【编辑" not in cleaned
    assert "潘功胜报告2025年10月以来金融工作情况" in cleaned


def test_clean_summary_drops_page_fragments():
    """面包屑、时间戳、来源行、字号切换、分享按钮都不是正文。"""
    raw = (
        "新华网 > 时政 > 正文\n\n"
        "2026-11-05 17:01:40  来源：新华网\n\n"
        "字体： 小 中 大\n\n"
        "分享到：\n\n"
        "新华社北京11月5日电　国务院总理李强日前签署国务院令，公布一批行政法规的修改决定。"
    )
    cleaned = _clean_summary(raw)

    assert "新华网 > 时政" not in cleaned
    assert "17:01:40" not in cleaned
    assert "字体：" not in cleaned
    assert "分享到" not in cleaned
    assert "新华社北京11月5日电" in cleaned


def test_clean_summary_drops_repeated_title():
    """页面顶部与正文开头重复的标题应去掉（标题已单独渲染）。"""
    title = "李强签署国务院令 公布《国务院关于修改和废止部分行政法规的决定》-新华网"
    raw = (
        f"{title}\n\n"
        "# 李强签署国务院令 公布《国务院关于修改和废止部分行政法规的决定》\n\n"
        "新华社北京11月5日电　国务院总理李强日前签署国务院令，公布《国务院关于修改和废止部分行政法规的决定》，自公布之日起施行。"
    )
    cleaned = _clean_summary(raw, title)

    assert "# 李强签署" not in cleaned
    assert not cleaned.startswith(title)
    assert "新华社北京11月5日电" in cleaned


def test_clean_summary_keeps_short_summary():
    """很短的正常摘要不能被当成页面碎片删掉。"""
    assert _clean_summary("摘要内容") == "摘要内容"
    assert _clean_summary("公司预计全年净利润同比增长。") == "公司预计全年净利润同比增长。"


def test_clean_summary_keeps_normal_text_untouched():
    """普通正文段落一个字符都不该被改。"""
    assert _clean_summary(_CORE) == _CORE


def test_clean_summary_keeps_english_paragraph():
    """英文段落不按中文导航规则处理。"""
    raw = (
        "Stock market surges on election results as investors weigh the "
        "implications of a divided congress for fiscal policy."
    )
    assert _clean_summary(raw) == raw


def test_format_items_marks_missing_body():
    """正文被清洗光时给出占位说明，避免模型据此编造内容。"""
    text = _format_items([_item("标题", "https://a.example/x", _NAV_BAR)], 10, 500)

    assert "未能抽取到正文" in text
    assert "操盘必读" not in text


def test_format_items_truncates_after_cleaning():
    """先清洗再截断：正文开头优先占据字符预算。"""
    raw = f"{_NAV_BAR}\n\n" + _CORE * 8
    text = _format_items([_item("标题", "https://a.example/x", raw)], 10, 100)

    assert "操盘必读" not in text
    assert "金融市场运行总体稳定" in text
    assert "…" in text


# ── 同事件去重 ────────────────────────────────────────────────────────


def test_dedupe_same_url_keeps_first():
    items = [
        _item("甲", "https://a.example/1", _CORE),
        _item("乙", "https://a.example/1", "完全不同的另一段内容，用来验证按 URL 判重是否生效。" * 3),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 1
    assert kept[0]["title"] == "甲"


def test_dedupe_title_with_site_suffix_and_punctuation():
    """「！央行…，信息量」与「，央行… 信息量」+ 不同站名后缀应判为同一条。"""
    items = [
        _item("重要报告！央行行长潘功胜发声，信息量巨大 - 21经济网", "https://a.example/1", ""),
        _item("重要报告，央行行长潘功胜发声，信息量巨大_凤凰网", "https://b.example/2", ""),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 1
    assert kept[0]["link"] == "https://a.example/1"


def test_dedupe_merges_similar_summary_and_keeps_richer_one():
    """同事件转载只留摘要更完整的那条，位置按首次出现。"""
    items = [
        _item("简版转载", "https://a.example/1", _CORE * 2),
        _item("详版转载", "https://b.example/2", _CORE * 3),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 1
    assert kept[0]["link"] == "https://b.example/2"


def test_dedupe_keeps_distinct_news_with_short_summary():
    """摘要过短时不参与相似度判重，否则「摘要内容」这种占位会被误合并。"""
    items = [
        _item("标题0", "https://a.example/0", "摘要内容"),
        _item("标题1", "https://a.example/1", "摘要内容"),
        _item("标题2", "https://a.example/2", "摘要内容"),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 3


def test_dedupe_keeps_different_topics():
    items = [
        _item("央行报告", "https://a.example/1", _CORE * 2),
        _item(
            "进博会开幕",
            "https://b.example/2",
            "第九届中国国际进口博览会在上海开幕，共有162个国家、地区和国际组织参加，"
            "企业展面积首次突破37万平方米，全球首发新品超过480件，创历年新高。",
        ),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 2


def test_dedupe_empty_body_does_not_collapse_results():
    """两条都只剩标题（正文全被清洗掉）时不能互相吞掉。"""
    items = [
        _item("交通银行亮相进博会", "https://a.example/1", _NAV_BAR),
        _item("中信建投补资本出海", "https://b.example/2", _NAV_BAR),
    ]
    kept = _dedupe_items(items)

    assert len(kept) == 2
