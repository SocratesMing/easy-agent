"""技能分类：用户级类别的持久化、自动归类与归属管理。

存储位置（用户级，与 ``mcp.json`` 同级）：:

    workspace/{username}/skill-categories.json

文件结构::

    {
      "version": 1,
      "categories": [
        {"id": "doc", "name": "文档处理", "order": 10, "builtin": true},
        {"id": "cat_1a2b3c4d", "name": "投资理财", "order": 100, "builtin": false}
      ],
      "assignments": {"pdf": "doc"}
    }

约定：

- 类别集合首次访问时用内置预置（``BUILTIN_CATEGORIES``）初始化并落盘，此后**完全以文件
  为准**——所以用户删掉内置类别后不会被自动补回；重命名、排序同理。
- 技能归属以 ``assignments`` 为准（用户手改，或技能创建时自动识别的结果）；
  没有记录时按关键词实时推断（不落盘，仅用于展示）。
- 自动识别：把技能名 + 描述 + SKILL.md 正文前若干字符做关键词打分，命中最高者获胜；
  内置类别用自己的关键词表，自定义类别用类别名参与匹配；全都命中不到则归入虚拟兜底
  类别 ``default``（「通用技能」，不可重命名/删除，也不在 ``categories`` 列表里）。
- 本模块只碰用户目录下的一个 JSON 文件，不依赖 Agent 运行期配置，不触发 Agent 缓存失效
  （分类只影响展示，不改变技能内容与加载行为）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from easy_agent.bootstrap.config import Config

logger = logging.getLogger("easy-agent")

#: 用户类别文件（位于 workspace/{username}/ 下）
CATEGORY_FILE_NAME = "skill-categories.json"

#: 虚拟兜底类别：不可重命名/删除，不进 categories 列表
DEFAULT_CATEGORY_ID = "default"
DEFAULT_CATEGORY_NAME = "通用技能"

#: 类别名长度上限（字符）
MAX_CATEGORY_NAME_LEN = 24

#: 自动归类时参与匹配的正文长度上限（避免大文件反复扫描）
MAX_MATCH_BODY_CHARS = 4000

#: 技能名 / 描述命中的权重倍数（正文里技术词汇出现概率高，不能与正文等权）
BOOST_WEIGHT = 5

#: 内置类别预置：首次使用时写入用户文件，并提供自动归类关键词
BUILTIN_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "doc",
        "name": "文档处理",
        "keywords": [
            "文档", "表格", "幻灯", "演示", "报告", "排版", "格式", "转换", "批注",
            "pdf", "docx", "word", "excel", "xlsx", "pptx", "powerpoint", "ppt",
            "markdown", "ocr", "epub", "csv 转", "模板",
        ],
    },
    {
        "id": "code",
        "name": "代码开发",
        "keywords": [
            "代码", "编程", "开发", "调试", "重构", "单元测试", "编译", "构建", "部署",
            "仓库", "脚本", "函数", "接口文档", "依赖",
            "python", "javascript", "typescript", "java", "golang", "rust", "shell",
            "git", "docker", "kubernetes", "pytest", "npm", "vite",
        ],
    },
    {
        "id": "design",
        "name": "设计创意",
        "keywords": [
            "设计", "视觉", "配色", "品牌", "图标", "插画", "海报", "原型", "主题",
            "字体", "封面", "logo", "ui", "ux", "figma", "canvas", "css 样式",
        ],
    },
    {
        "id": "data",
        "name": "数据分析",
        "keywords": [
            "数据", "分析", "统计", "报表", "图表", "指标", "可视化", "清洗", "建模",
            "预测", "爬取", "爬虫", "抓取", "聚合", "透视",
            "sql", "pandas", "numpy", "excel 分析", "bi", "etl",
        ],
    },
    {
        "id": "api",
        "name": "API集成",
        "keywords": [
            "集成", "对接", "第三方", "回调", "鉴权", "令牌",
            "api", "webhook", "oauth", "sdk", "http 请求", "rest", "graphql",
            "mcp server", "slack", "飞书", "钉钉", "企业微信", "github api",
        ],
    },
    {
        "id": "office",
        "name": "办公协同",
        "keywords": [
            "办公", "协同", "会议", "日程", "邮件", "审批", "流程", "报销", "制度",
            "周报", "日报", "月报", "纪要", "通知", "招聘", "绩效", "排班", "请假",
        ],
    },
    {
        "id": "content",
        "name": "内容创作",
        "keywords": [
            "写作", "文案", "文章", "标题", "营销", "推广", "翻译", "润色", "配音",
            "字幕", "短视频", "公众号", "小红书", "知乎", "博客", "播客", "脚本创作",
        ],
    },
    {
        "id": "tools",
        "name": "效率工具",
        "keywords": [
            "效率", "自动化", "批处理", "定时", "监控", "备份", "整理", "归档", "检索",
            "快捷", "批量", "助手", "清单", "todo", "cron",
        ],
    },
]

_BUILTIN_BY_ID = {item["id"]: item for item in BUILTIN_CATEGORIES}

#: 类别 id 生成用（自定义类别）
_ID_PREFIX = "cat_"
_ID_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")


# ── 存储 ──────────────────────────────────────────────────────────────


def store_path(username: str) -> Path:
    """用户类别文件路径（不保证存在）。"""
    return Config.get_user_workspace_dir(username) / CATEGORY_FILE_NAME


def _builtin_seed() -> list[dict[str, Any]]:
    """内置预置：首次初始化时写入用户文件。"""
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "order": (index + 1) * 10,
            "builtin": True,
        }
        for index, item in enumerate(BUILTIN_CATEGORIES)
    ]


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "categories": _builtin_seed(), "assignments": {}}


def _normalize_store(raw: Any) -> dict[str, Any]:
    """容错解析：结构不合法时回退为空预置，避免一个坏文件让技能中心整体不可用。"""
    if not isinstance(raw, dict):
        return _empty_store()

    categories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("categories") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not cid or not name or cid in seen or not _ID_RE.match(cid):
            continue
        seen.add(cid)
        try:
            order = int(item.get("order", 0))
        except (TypeError, ValueError):
            order = 0
        categories.append({
            "id": cid,
            "name": name,
            "order": order,
            "builtin": bool(item.get("builtin", cid in _BUILTIN_BY_ID)),
        })
    if not categories:
        categories = _builtin_seed()

    assignments: dict[str, str] = {}
    for dir_name, cid in (raw.get("assignments") or {}).items():
        if isinstance(dir_name, str) and isinstance(cid, str) and cid:
            assignments[dir_name] = cid

    return {"version": 1, "categories": categories, "assignments": assignments}


def load_store(username: str) -> dict[str, Any]:
    """读取用户类别文件；不存在或损坏时用内置预置并落盘（幂等初始化）。"""
    path = store_path(username)
    if not path.exists():
        store = _empty_store()
        save_store(username, store)
        return store

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"技能分类文件不可读，按内置预置处理 | 用户: {username} | {path} | {e}")
        return _empty_store()

    return _normalize_store(raw)


def save_store(username: str, store: dict[str, Any]) -> None:
    """原子写入类别文件（先写临时文件再 rename，避免半截 JSON）。"""
    path = store_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


# ── 类别读取 ──────────────────────────────────────────────────────────


def list_categories(username: str) -> list[dict[str, Any]]:
    """按 order 升序列出类别（不含虚拟兜底类别）。"""
    store = load_store(username)
    return sorted(store["categories"], key=lambda item: (item["order"], item["name"]))


# ── 类别编辑 ──────────────────────────────────────────────────────────


def _validate_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("类别名不能为空")
    if len(cleaned) > MAX_CATEGORY_NAME_LEN:
        raise ValueError(f"类别名过长（上限 {MAX_CATEGORY_NAME_LEN} 字符）")
    return cleaned


def _new_id() -> str:
    return f"{_ID_PREFIX}{uuid.uuid4().hex[:8]}"


def create_category(username: str, name: str) -> dict[str, Any]:
    """新建自定义类别，追加到末尾（order 取当前最大值 + 10）。"""
    cleaned = _validate_name(name)
    store = load_store(username)
    if any(item["name"] == cleaned for item in store["categories"]):
        raise ValueError(f"类别「{cleaned}」已存在")

    max_order = max((item["order"] for item in store["categories"]), default=0)
    category = {
        "id": _new_id(),
        "name": cleaned,
        "order": max_order + 10,
        "builtin": False,
    }
    store["categories"].append(category)
    save_store(username, store)
    logger.info(f"技能类别创建 | 用户: {username} | {cleaned} | id={category['id']}")
    return category


def rename_category(username: str, category_id: str, name: str) -> dict[str, Any]:
    """重命名类别（内置类别也可改名，只影响展示）。"""
    cleaned = _validate_name(name)
    store = load_store(username)
    target = next((item for item in store["categories"] if item["id"] == category_id), None)
    if target is None:
        raise KeyError(category_id)
    if any(item["name"] == cleaned and item["id"] != category_id for item in store["categories"]):
        raise ValueError(f"类别「{cleaned}」已存在")

    target["name"] = cleaned
    save_store(username, store)
    logger.info(f"技能类别重命名 | 用户: {username} | {category_id} -> {cleaned}")
    return target


def delete_category(username: str, category_id: str) -> dict[str, Any]:
    """删除类别；该类别的技能归属一并清除（回落到自动归类）。"""
    store = load_store(username)
    target = next((item for item in store["categories"] if item["id"] == category_id), None)
    if target is None:
        raise KeyError(category_id)

    store["categories"] = [item for item in store["categories"] if item["id"] != category_id]
    dropped = [
        dir_name
        for dir_name, cid in store["assignments"].items()
        if cid == category_id
    ]
    for dir_name in dropped:
        store["assignments"].pop(dir_name, None)
    save_store(username, store)
    logger.info(
        f"技能类别删除 | 用户: {username} | {target['name']} | "
        f"清空归属的技能: {dropped}"
    )
    return {"id": category_id, "name": target["name"], "unassigned": dropped}


def reorder_categories(username: str, category_ids: list[str]) -> list[dict[str, Any]]:
    """按给定顺序重排类别（order 按 10 递增）；未列出的类别排在最后，保持原相对顺序。"""
    store = load_store(username)
    known = {item["id"]: item for item in store["categories"]}

    ordered: list[dict[str, Any]] = []
    for cid in category_ids:
        item = known.pop(cid, None)
        if item is not None:
            ordered.append(item)
    ordered.extend(sorted(known.values(), key=lambda item: (item["order"], item["name"])))

    for index, item in enumerate(ordered):
        item["order"] = (index + 1) * 10
    store["categories"] = ordered
    save_store(username, store)
    logger.info(f"技能类别排序 | 用户: {username} | {[item['id'] for item in ordered]}")
    return ordered


def assign_skill(username: str, dir_name: str, category_id: str) -> str:
    """设置技能归属；``category_id`` 为空表示清除显式归属（回落自动归类）。

    返回落库后的归属 id（清除时为推断结果），类别不存在抛 KeyError。
    """
    cleaned_dir = (dir_name or "").strip()
    if not cleaned_dir:
        raise ValueError("技能目录名不能为空")

    store = load_store(username)
    if category_id:
        # 「通用技能」是虚拟兜底类别：也允许显式指定，落库为 default，避免又被自动归类拉走
        if category_id != DEFAULT_CATEGORY_ID and not any(
            item["id"] == category_id for item in store["categories"]
        ):
            raise KeyError(category_id)
        store["assignments"][cleaned_dir] = category_id
    else:
        store["assignments"].pop(cleaned_dir, None)

    save_store(username, store)
    logger.info(
        f"技能归属更新 | 用户: {username} | 技能: {cleaned_dir} | "
        f"类别: {category_id or '(自动)'}"
    )
    # 清除归属时返回空串，由调用方按技能内容重新推断（这里拿不到技能正文）
    return category_id


# ── 自动归类 ──────────────────────────────────────────────────────────


def _keywords_for(category: dict[str, Any]) -> list[str]:
    """类别参与匹配的关键词：内置类别的关键词表 + 类别名本身。"""
    keywords = list(_BUILTIN_BY_ID.get(category["id"], {}).get("keywords", []))
    keywords.append(str(category["name"]))
    return [kw.lower() for kw in keywords if kw]


def infer_category_id(
    store: dict[str, Any], text: str, boost_text: str = ""
) -> str:
    """按关键词打分推断类别 id；无任何命中返回 ``default``。

    ``boost_text``（目录名 + 技能名 + 描述）里的命中按 ``BOOST_WEIGHT`` 倍计分：技能名与
    描述最能说明技能属于哪一类，而长技能的正文里技术词汇（python / git / npm…）出现概率
    很高，等权参与会把「文档处理」（docx，正文 20KB 且带脚本）误判成「代码开发」。
    """
    haystack = (text or "").lower()
    boosted = (boost_text or "").lower()
    if not haystack and not boosted:
        return DEFAULT_CATEGORY_ID

    best_id = DEFAULT_CATEGORY_ID
    best_score = 0
    categories = sorted(store["categories"], key=lambda item: (item["order"], item["name"]))
    for category in categories:
        score = 0
        for keyword in _keywords_for(category):
            if not keyword:
                continue
            # 长关键词更具区分度（"pdf" 比 "文档" 更能说明问题）
            weight = 2 if len(keyword) >= 4 else 1
            if keyword in boosted:
                score += weight * BOOST_WEIGHT
            elif keyword in haystack:
                score += weight
        if score > best_score:
            best_id, best_score = category["id"], score
    return best_id


def _match_text(dir_name: str, name: str = "", description: str = "") -> str:
    """高权重匹配文本：目录名 + 技能名 + 描述（正文单独传，权重较低）。"""
    return " ".join(part for part in (dir_name, name, description) if part)


def resolve_from_store(
    store: dict[str, Any],
    dir_name: str,
    name: str = "",
    description: str = "",
    body: str = "",
) -> str:
    """基于已加载的 store 解析技能归属（批量解析时避免反复读文件）。"""
    assigned = store["assignments"].get(dir_name)
    if assigned == DEFAULT_CATEGORY_ID:
        return DEFAULT_CATEGORY_ID
    if assigned and any(item["id"] == assigned for item in store["categories"]):
        return assigned
    return infer_category_id(
        store,
        (body or "")[:MAX_MATCH_BODY_CHARS],
        _match_text(dir_name, name, description),
    )


def auto_assign_skill(
    username: str,
    dir_name: str,
    name: str = "",
    description: str = "",
    body: str = "",
) -> str:
    """技能创建（添加公共技能 / 导入 zip / 对话创建）时自动归类并落盘。

    分类在创建时**一次定稿**：即使推断结果是「通用技能」也会写入归属记录，之后不再
    随技能内容变化而变动；已有归属时不覆盖。
    """
    store = load_store(username)
    if store["assignments"].get(dir_name):
        return store["assignments"][dir_name]

    category_id = infer_category_id(
        store,
        (body or "")[:MAX_MATCH_BODY_CHARS],
        _match_text(dir_name, name, description),
    )
    store["assignments"][dir_name] = category_id
    save_store(username, store)
    logger.info(
        f"技能自动归类 | 用户: {username} | 技能: {dir_name} | 类别: {category_id}"
    )
    return category_id


def auto_assign_skill_dir(username: str, dir_name: str, skill_dir: Path) -> str:
    """按技能目录自动归类（读元数据 + 正文），供创建技能后调用。"""
    from easy_agent.services import skill_library

    metadata = skill_library.skill_metadata(skill_dir)
    _, content = skill_library.read_skill_document(skill_dir)
    return auto_assign_skill(
        username,
        dir_name,
        name=metadata.get("name", ""),
        description=metadata.get("description", ""),
        body=content,
    )
