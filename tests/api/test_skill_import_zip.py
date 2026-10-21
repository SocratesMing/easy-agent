"""技能 zip 导入接口测试（/agent/skill-center/import-skill）：校验、三种压缩包结构、落盘与清理。"""

import io
import shutil
import zipfile

import pytest

from easy_agent.api import skill_center
from easy_agent.services import skill_library

URL = "/agent/skill-center/import-skill"
SKILL_MD = "---\nname: demo\ndescription: 演示技能\n---\n\n# Demo\n"


@pytest.fixture(autouse=True)
def _clean_user_skills(client):
    """依赖 client：用户工作区此时已重定向到临时目录。"""
    root = skill_library.user_skills_dir("testuser")
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _post(client, data: bytes, filename: str = "skill.zip"):
    return client.post(URL, files={"file": (filename, data, "application/zip")})


def _leftover_tmp_dirs(root):
    return [p.name for p in root.iterdir() if p.name.startswith(".tmp_import_")] if root.exists() else []


@pytest.mark.parametrize(
    "filename, data, detail",
    [
        ("skill.tar", b"x", "仅支持 .zip 压缩包"),
        ("skill.zip", b"", "压缩包为空"),
        ("skill.zip", b"not a zip", "无效的 zip 文件"),
    ],
)
def test_rejects_bad_uploads(client, filename, data, detail):
    resp = _post(client, data, filename)
    assert resp.status_code == 400
    assert resp.json()["detail"] == detail


@pytest.mark.parametrize("name", ["../evil.md", "/abs/evil.md", "C:/evil.md", "a/../../evil.md"])
def test_rejects_unsafe_entry_paths(client, name):
    resp = _post(client, _zip({name: "x", "demo/SKILL.md": SKILL_MD}))
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"压缩包含非法路径: {name}"


def test_single_directory_layout_keeps_the_directory_name(client, _clean_user_skills):
    resp = _post(client, _zip({"my-skill/SKILL.md": SKILL_MD, "my-skill/scripts/run.py": "print(1)"}))

    assert resp.status_code == 200
    body = resp.json()
    assert body["dir_name"] == "my-skill"
    assert body["category_id"]
    target = _clean_user_skills / "my-skill"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert (target / "scripts" / "run.py").is_file()
    assert _leftover_tmp_dirs(_clean_user_skills) == []


def test_flat_layout_takes_the_normalized_zip_name(client, _clean_user_skills):
    data = _zip({"SKILL.md": SKILL_MD, "helper.py": "x", "__MACOSX/._SKILL.md": "junk"})

    resp = _post(client, data, filename="Report Tool!.zip")

    assert resp.status_code == 200
    assert resp.json()["dir_name"] == "Report_Tool"
    target = _clean_user_skills / "Report_Tool"
    assert sorted(p.name for p in target.iterdir()) == ["SKILL.md", "helper.py"]
    assert _leftover_tmp_dirs(_clean_user_skills) == []


def test_picks_the_only_directory_that_has_a_skill_doc(client, _clean_user_skills):
    resp = _post(client, _zip({"docs/notes.txt": "x", "real/README.md": "# Real\n"}))

    assert resp.status_code == 200
    assert resp.json()["dir_name"] == "real"
    assert (_clean_user_skills / "real" / "README.md").is_file()


@pytest.mark.parametrize(
    "entries, detail",
    [
        ({"a/x.txt": "", "b/y.txt": ""}, "未找到有效技能目录（需包含 SKILL.md 或 README.md）"),
        ({"only/x.txt": ""}, "技能目录必须包含 SKILL.md 或 README.md"),
    ],
)
def test_rejects_archives_without_a_skill_doc(client, _clean_user_skills, entries, detail):
    resp = _post(client, _zip(entries))

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert _leftover_tmp_dirs(_clean_user_skills) == []


def test_existing_skill_is_a_conflict(client):
    data = _zip({"my-skill/SKILL.md": SKILL_MD})
    assert _post(client, data).status_code == 200

    resp = _post(client, data)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "技能 'my-skill' 已存在，请先移除或重命名压缩包"


def test_size_and_count_limits(client, monkeypatch):
    data = _zip({"my-skill/SKILL.md": SKILL_MD, "my-skill/a.txt": "x" * 100})

    monkeypatch.setattr(skill_center, "MAX_FILE_COUNT", 1)
    resp = _post(client, data)
    assert (resp.status_code, resp.json()["detail"]) == (400, "压缩包内条目过多，上限 1")

    monkeypatch.setattr(skill_center, "MAX_FILE_COUNT", 1000)
    monkeypatch.setattr(skill_center, "MAX_EXTRACT_SIZE", 50)
    resp = _post(client, data)
    assert (resp.status_code, resp.json()["detail"]) == (400, "解压后内容过大")

    monkeypatch.setattr(skill_center, "MAX_ZIP_SIZE", 10)
    resp = _post(client, data)
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("压缩包过大")
