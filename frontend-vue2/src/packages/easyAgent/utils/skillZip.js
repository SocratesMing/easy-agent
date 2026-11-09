/**
 * 上传技能压缩包的前端校验：两版前端（frontend / frontend-vue2）逐字一致，可单独测试。
 *
 * 只拦「前端能判断的两件事」，不做解压：
 *
 * 1. 必须是 `.zip`；
 * 2. 技能包名称（压缩包文件名去掉扩展名）只允许小写字母、数字与单个连字符。
 *
 * 为什么要在前端拦：后端 `api/skill_center.py::_validate_skill_dir_name()` 用同一套
 * 正则（`agent/skills.py::SKILL_NAME_RE`）兜底，不合规直接 400。前端提前提示只为省掉
 * 一次「上传→解压→被拒」的往返，并能当场说清哪里不对、应该写成什么样。
 *
 * 名称规则与官方 Agent Skills 规范一致（`^[a-z0-9]+(-[a-z0-9]+)*$`）：不允许大写、
 * 下划线、空格，也不允许开头/结尾/连续出现连字符。
 */

/** 技能包名称：小写字母与数字的分段，用单个连字符连接 */
export const SKILL_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/

/** 名称长度上限（与 `easy_agent/agent/skills.py::MAX_SKILL_NAME_CHARS` 一致） */
export const MAX_SKILL_NAME_CHARS = 64

/**
 * 从文件名取技能包名称（去目录前缀与 `.zip` 扩展名）。
 *
 * @param {string} fileName 形如 `pdf-convert.zip` 或 `C:\\fakepath\\pdf-convert.zip`
 * @returns {string} 形如 `pdf-convert`；无法取出时返回空串
 */
export function skillNameFromZip(fileName) {
  const base = String(fileName || '').split(/[\\/]/).pop() || ''
  return base.replace(/\.zip$/i, '').trim()
}

/**
 * 校验待上传的技能包。
 *
 * @param {{ name?: string, size?: number } | null | undefined} file 选中文件
 * @returns {string | null} 错误文案；`null` 表示通过
 */
export function validateSkillZip(file) {
  const fileName = String((file && file.name) || '').trim()
  if (!fileName) return '请选择要上传的技能压缩包'
  if (!/\.zip$/i.test(fileName)) {
    return `「${fileName}」不是 .zip 压缩包，请打包成 zip 后再上传`
  }

  const name = skillNameFromZip(fileName)
  if (!name) return '技能包名称不能为空'
  if (name.length > MAX_SKILL_NAME_CHARS) {
    return `技能包名称不能超过 ${MAX_SKILL_NAME_CHARS} 个字符`
  }
  if (!SKILL_NAME_RE.test(name)) {
    return `技能包名称「${name}」只能用 小写字母、数字 和单个连字符（-）`
  }
  return null
}
