<!--
  专家列表（「专家 · 技能 · 连接器」容器的专家 Tab 主视图）。

  两个视图（面板内切换，不改动 SkillCenter.vue）：
  - **公共专家（默认）**：预置专家库，来自后端 exports 目录（GET /agent/public-list）。
    保留完整的浏览体验：搜索 + 分类 chips（tag_name 展示、tag_id 过滤）。
    卡片只读：无「编辑 / 删除」，状态位显示「预置」。
  - **我的专家**：自己创建的专家（GET /agent/list），**仅列表**——无搜索、无分类筛选，
    卡片编辑/删除可用。入口是工具条「新建专家」右侧的「我的专家」按钮。

  样式与交互对齐 SkillListPanel.vue（技能 Tab）：
  - 工具条：搜索 / 新建专家 / 我的专家
  - 分类 chips：全部 + 配置里的标签（tag_name 展示、tag_id 过滤，见 /agent/list 的 tags）
  - 卡片：头像 / 名称 + 状态徽标 / 单行功能描述（export_description）/ 技能标签
    （export_skills_names，最多 3 个）/ 底部 版本·更新时间·分类
  - 点「召唤」/ 点示例问题 → emit('use', item, prompt?)，由 App.vue 挂载专家胶囊
    （带 prompt 时回填输入框）并走 /agent/chat/stream（带 expert_agent_id）
  - 点卡片 → 专家详情弹窗（宽版三标签页：专家设定 MD[只读渲染/原文] / 定义提示词
    [export_prompt 原文] / 概要信息[功能描述·绑定技能包·专家帮你做·版本与 ID]）

  新建 / 编辑专家（与「添加技能」同一套跳转，见 SkillListPanel.handleCreateSkill）：
  - 「新建专家」→ 确保 expert-creator 技能已安装 → 草稿写入 sessionStorage
    （开头是「用「expert-creator」技能帮我创建：」，替代内联的 /expert-creator
    标签；技能以 insert_token:false 预选，发送时随 skills 字段激活）→
    `emit('create-expert')`，由 App 新开会话；ChatInput 挂载时恢复草稿与预选技能；
  - 卡片「编辑」→ 同上，但预填的是**当前专家参数 + 专家 ID** 的修改模板 →
    `emit('edit-expert')`，由 App 决定「当前窗口是新会话就带入、已有对话则新开会话」。

  数据与后端接口（easy_agent/api/expert.py）：
  GET /agent/public-list（公共专家目录，字段与 /agent/list 一致，另带 scope="public"）
  GET /agent/list（keyword 服务端模糊搜索；已删除（软删除）的专家不在列表展示；
  响应里的 tags 为 config.yaml 的 expert_tags 候选）
-->
<template>
  <div class="expert-panel">
    <div class="expert-toolbar">
      <!-- 公共专家视图：搜索框（我的专家视图不提供搜索，改为「返回」入口） -->
      <div v-if="view === 'public'" class="search-box">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          v-model="keyword"
          type="text"
          placeholder="搜索专家"
          aria-label="搜索专家"
          spellcheck="false"
          autocomplete="off"
          @keyup.enter="refresh"
        />
        <button v-if="keyword" class="search-clear" aria-label="清空搜索" @click="clearKeyword">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <button v-else class="back-btn" title="返回公共专家列表" @click="switchView('public')">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </svg>
        返回公共专家
      </button>

      <!-- 新建专家：仅公共专家视图展示（我的专家视图按需求去掉该入口，
           需要创建时经「返回公共专家」回到公共视图）。

           点它不再打开弹窗，而是：确保 expert-creator 技能已安装 → 把创建模板预填进
           输入框 → 关闭面板回到对话页，由 Agent 在对话里完成创建（与「添加技能」一致）。 -->
      <button
        v-if="view === 'public'"
        class="action-btn-primary"
        :disabled="preparing"
        title="在对话框里由 Agent 帮你创建专家"
        @click="handleCreateExpert"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        新建专家
      </button>

      <!-- 我的专家：与「新建专家」同款主色按钮（mine-btn 只调间距，保证两按钮紧挨且同高） -->
      <button
        v-if="view === 'public'"
        class="action-btn-primary mine-btn"
        title="查看我创建的专家（可编辑、删除）"
        @click="switchView('mine')"
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
        我的专家
      </button>
    </div>

    <!-- 分类 chips：仅公共专家视图提供（我的专家视图按需求只做纯列表，
         不给搜索与分类筛选） -->
    <div v-if="view === 'public'" class="status-chips" role="group" aria-label="按分类筛选">
      <button
        class="chip"
        :class="{ active: tagFilter === 'all' }"
        :aria-pressed="tagFilter === 'all' ? 'true' : 'false'"
        @click="tagFilter = 'all'"
      >
        全部
        <span v-if="experts.length" class="chip-count">{{ experts.length }}</span>
      </button>
      <button
        v-for="item in tagChips"
        :key="item.tag_id"
        class="chip"
        :class="{ active: tagFilter === item.tag_id }"
        :aria-pressed="tagFilter === item.tag_id ? 'true' : 'false'"
        @click="tagFilter = item.tag_id"
      >
        {{ item.tag_name }}
        <span v-if="tagCounts[item.tag_id]" class="chip-count">{{ tagCounts[item.tag_id] }}</span>
      </button>
    </div>

    <!-- 我的专家视图的说明条：顶替 chips 行，保持两种视图的纵向节奏一致 -->
    <div v-else class="view-bar">
      <span class="view-title">我的专家</span>
      <span v-if="experts.length" class="chip-count">{{ experts.length }}</span>
      <span class="view-tip">仅展示你自己创建的专家，可编辑与删除</span>
    </div>


    <div class="expert-center-content">
      <div v-if="loading" class="state-box">
        <div class="spinner"></div>
        <span>加载中…</span>
      </div>

      <div v-else-if="error" class="state-box">
        <p>{{ error }}</p>
        <button class="action-btn-outline" @click="refresh">重试</button>
      </div>

      <div v-else-if="filteredExperts.length === 0" class="state-box">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
        <p>{{ emptyText }}</p>
      </div>

      <div v-else class="experts-grid">
        <div
          v-for="item in filteredExperts"
          :key="item.export_agent_id"
          class="expert-card"
          role="button"
          tabindex="0"
          title="查看专家详情"
          @click="openDetail(item)"
          @keydown.enter="openDetail(item)"
        >
          <div class="expert-card-inner">
            <div class="expert-avatar" :class="avatarColorClass(item)" aria-hidden="true">{{ avatarChar(item) }}</div>
            <div class="expert-card-body">
              <div class="expert-card-name-row">
                <span class="expert-card-name" :title="item.export_name_cn">{{ item.export_name_cn }}</span>
                <!-- 公共（预置）专家：只读，状态位固定显示「预置」 -->
                <span v-if="isPublic(item)" class="expert-tag">预置</span>
                <span v-else-if="item.agent_status === 'active'" class="expert-tag">可用</span>
                <span v-else-if="item.agent_status === 'building'" class="expert-tag warn">生成中</span>
                <span v-else-if="item.agent_status === 'failed'" class="expert-tag off">生成失败</span>
                <span v-else class="expert-tag off">已删除</span>
              </div>
              <span class="expert-card-desc" :title="cardDesc(item)">
                {{ cardDesc(item) }}
              </span>
            </div>
            <button
              class="use-btn"
              :disabled="item.agent_status !== 'active'"
              :title="item.agent_status === 'active' ? '在当前会话中召唤该专家' : '仅「可用」状态的专家可以召唤'"
              @click.stop="emit('use', item)"
            >
              召唤
            </button>
          </div>

          <!-- 技能标签：export_skills_names，最多展示 3 个（超出用 +n 收起） -->
          <div v-if="skillTagsOf(item).length" class="card-skill-tags">
            <span v-for="tag in skillTagsOf(item).slice(0, 3)" :key="tag" class="skill-tag">{{ tag }}</span>
            <span v-if="skillTagsOf(item).length > 3" class="skill-tag more">+{{ skillTagsOf(item).length - 3 }}</span>
          </div>

          <div class="expert-card-footer">
            <span>v{{ item.export_version ?? 1 }}</span>
            <span class="dot">·</span>
            <span>{{ formatTime(item.update_timestamp) }}</span>
            <template v-if="tagNameOf(item.tag_id)">
              <span class="dot">·</span>
              <span class="footer-tag">{{ tagNameOf(item.tag_id) }}</span>
            </template>
            <span class="footer-actions">
              <!-- 公共（预置）专家只读：不给编辑/删除入口（内容随发行包更新）。
                   「编辑」同样改为把当前专家参数带入输入框、由 Agent 在对话里改。 -->
              <template v-if="!isPublic(item)">
                <button
                  class="footer-btn"
                  :disabled="item.agent_status === 'disabled' || preparing"
                  title="在对话框里由 Agent 帮你修改该专家"
                  @click.stop="handleEditExpert(item)"
                >编辑</button>
                <button class="footer-btn danger" :disabled="item.agent_status === 'disabled'" @click.stop="confirmDelete(item)">删除</button>
              </template>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 专家详情弹窗：点卡片打开；右上角 × 或点击遮罩（弹窗外的灰色区域）均可关闭。
         新建 / 编辑不在本面板：都改到对话框里由 Agent 完成（handleCreateExpert / handleEditExpert）。 -->
    <div v-if="detailItem" class="modal-mask" @click.self="closeDetail">
      <div class="modal detail-modal" role="dialog" aria-modal="true" aria-label="专家详情">
        <button class="modal-close detail-close" aria-label="关闭详情" @click="closeDetail">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        <div class="detail-head">
          <div class="detail-avatar" :class="avatarColorClass(detailItem)" aria-hidden="true">{{ avatarChar(detailItem) }}</div>
          <div class="detail-head-text">
            <div class="detail-name-row">
              <span class="detail-name" :title="detailItem.export_name_cn">{{ detailItem.export_name_cn }}</span>
              <span v-if="detailItem.export_name_en" class="detail-name-en">| {{ detailItem.export_name_en }}</span>
            </div>
            <div v-if="skillTagsOf(detailItem).length" class="detail-tags detail-head-tags">
              <span v-for="tag in skillTagsOf(detailItem)" :key="tag" class="skill-tag">{{ tag }}</span>
            </div>
          </div>
          <div class="detail-head-actions">
            <button
              class="summon-btn"
              :disabled="detailItem.agent_status !== 'active'"
              :title="detailItem.agent_status === 'active' ? '在当前会话中召唤该专家' : '仅「可用」状态的专家可以召唤'"
              @click="summon(detailItem)"
            >
              召唤
            </button>
          </div>
        </div>

        <!-- 三个标签页（默认「概要信息」）：概要 / 逻辑定义 / 定义提示词。
             全部只读；「定义提示词」仅本人创建的专家才有（公共预置专家由发行包提供）。 -->
        <div class="detail-tabs" role="tablist">
          <button
            class="detail-tab"
            :class="{ active: detailTab === 'info' }"
            role="tab"
            :aria-selected="detailTab === 'info'"
            @click="detailTab = 'info'"
          >概要信息</button>
          <button
            class="detail-tab"
            :class="{ active: detailTab === 'md' }"
            role="tab"
            :aria-selected="detailTab === 'md'"
            @click="detailTab = 'md'"
          >专家逻辑定义</button>
          <button
            v-if="showPromptTab"
            class="detail-tab"
            :class="{ active: detailTab === 'prompt' }"
            role="tab"
            :aria-selected="detailTab === 'prompt'"
            @click="detailTab = 'prompt'"
          >定义提示词</button>
          <span class="detail-tabs-hint">只读 · 不可编辑</span>
        </div>

        <div class="detail-body">
          <!-- 专家逻辑定义：运行时注入的系统人设原文（frontmatter + 角色设定） -->
          <div v-if="detailTab === 'md'" class="detail-md-pane">
            <div class="detail-md-bar">
              <span class="detail-md-file">{{ detailMd.fileName || '专家提示词.md' }}</span>
              <button
                v-if="detailMd.content"
                class="detail-md-raw-btn"
                @click="detailMdRaw = !detailMdRaw"
              >{{ detailMdRaw ? '查看渲染效果' : '查看原文' }}</button>
            </div>
            <div v-if="detailMd.loading" class="detail-md-state">正在加载专家提示词…</div>
            <div v-else-if="detailMd.error" class="detail-md-state is-error">{{ detailMd.error }}</div>
            <pre v-else-if="detailMdRaw" class="detail-md-code">{{ detailMd.content }}</pre>
            <div v-else class="detail-md-render markdown-body" v-html="detailMdHtml"></div>
          </div>

          <!-- 定义提示词：创建时用户输入的原始描述（export_prompt，未加工）。
               公共预置专家没有这一项（tab 也已隐藏），条件里再兜一层防止残留状态。 -->
          <div v-else-if="detailTab === 'prompt' && showPromptTab" class="detail-prompt-pane">
            <p class="detail-pane-hint">创建该专家时你输入的原始描述（原文，未经模型加工）</p>
            <pre v-if="detailItem.export_prompt" class="detail-prompt-text">{{ detailItem.export_prompt }}</pre>
            <p v-else class="detail-pane-empty">该专家未记录创建时的输入。</p>
          </div>

          <!-- 概要信息：功能描述 / 绑定技能包 / 示例问题 / 版本与 ID -->
          <div v-else class="detail-info-pane">
            <!-- 功能描述：若大模型给的是一条一条的，则逐行展示（见 descriptionLines） -->
            <div class="detail-desc-block">
              <p v-for="(line, idx) in descriptionLines(detailItem)" :key="idx" class="detail-desc">
                {{ line }}
              </p>
            </div>

            <!-- 绑定技能包（export_required_skills）：真实技能目录名，对话时只挂这些。
                 与卡片上的「技能标签」（展示用中文短标签）语义不同，故单独一段。 -->
            <div v-if="requiredSkillsOf(detailItem).length" class="detail-section">
              <h4 class="detail-section-title">
                <span class="section-icon" aria-hidden="true">🔒</span> 绑定技能包
              </h4>
              <div class="detail-tags detail-skill-list">
                <span v-for="name in requiredSkillsOf(detailItem)" :key="name" class="skill-tag">{{ name }}</span>
              </div>
              <div class="detail-skills-hint">该专家对话时优先使用以上技能包</div>
            </div>

            <div v-if="featuresOf(detailItem).length" class="detail-section">
              <h4 class="detail-section-title">
                <span class="section-icon" aria-hidden="true">💡</span> 专家帮你做
              </h4>
              <button
                v-for="(f, idx) in featuresOf(detailItem)"
                :key="idx"
                class="feature-row"
                :title="'召唤专家并填入该问题'"
                @click="summonWithPrompt(detailItem, f)"
              >
                <span class="feature-text">“{{ f }}”</span>
                <svg class="feature-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
                </svg>
              </button>
            </div>

            <div class="detail-section">
              <h4 class="detail-section-title">
                <span class="section-icon" aria-hidden="true">⋮⋮</span> 更多
              </h4>
              <div class="more-row">版本: v{{ detailVersion(detailItem) }}</div>
              <div class="more-row">ID: {{ detailItem.export_name_en || detailItem.export_agent_id }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { deleteExpert, getExpertMd, listExperts, listPublicExperts } from '../../../api/expert.js'
import {
  createMarkdownRenderer,
  escapeHtml,
  renderMarkdown,
  setupMarkedExtensions,
} from '../../../markdownSetup.js'
import { expertAvatarChar, expertAvatarClass } from '../../../utils/expertAvatar.js'
import { addSkillToUser, getUserSkills } from '../../../api/skills.js'

const props = defineProps({
  /** 当前会话 ID（保留：面板不再直接创建专家，创建走对话页的 Agent 流程） */
  sessionId: { type: String, default: '' },
})
const emit = defineEmits(['use', 'create-expert', 'edit-expert'])

/** 用户侧创建引导词模板（可后续迁移到提示词目录做成可配置） */
const CREATE_TEMPLATE =
  '帮我创建一个{name}专家，擅长 XXX。\n' +
  '我的经验是：[请补充你的行业背景、相关经验]\n' +
  '典型使用场景：[请描述你希望在什么情况下使用它]\n' +
  '期望输出：[请描述你希望它产出什么形式的结果]'

/** 草稿开头的一句自然语言点名（替代原先内联的 /expert-creator 标签） */
const CREATE_LEAD = '用「expert-creator」技能帮我创建：'
const EDIT_LEAD = '用「expert-creator」技能帮我修改：'

// ── 对话内创建 / 修改专家（与「添加技能」同一套跳转机制）─────────────────
// 面板只负责：确保 expert-creator 技能已安装 → 把模板写进 sessionStorage →
// 上抛事件让 App 决定用当前窗口还是新开窗口。真正的创建由 ChatInput 挂载时
// 恢复草稿并把技能记为「预选」（草稿开头那句话已点名技能，不再插 /标签），
// 发送时随 skills 字段交给后端激活，再由 Agent 调 save_expert 完成。
/** 输入框草稿键（与 ChatInput 的 DRAFT_KEY 一致） */
const DRAFT_KEY = 'easy_agent_input_draft'
/** 待选技能键（与 ChatInput 的 PENDING_SKILL_KEY 一致） */
const PENDING_SKILL_KEY = 'easy_agent_pending_skill'
/** 待修改专家键（与 ChatInput 的 PENDING_EXPERT_EDIT_KEY 一致）：
 *  专家 ID 走这里随请求结构化字段上行，**不进消息正文**（用户不可见、不可改） */
const PENDING_EXPERT_EDIT_KEY = 'easy_agent_pending_expert_edit'
/** 创建/修改专家所依赖的技能目录名 */
const CREATE_EXPERT_SKILL_DIR = 'expert-creator'
/** 正在准备（安装技能 / 写草稿）时禁用入口，避免连点 */
const preparing = ref(false)

/**
 * 把提示词模板与技能标签写入 sessionStorage（ChatInput 挂载时恢复）。
 *
 * `skill` 为技能列表里的真实记录。`insert_token: false` 告诉 ChatInput
 * **不要**往输入框插 `/expert-creator` 标签——草稿开头的
 * 「用「expert-creator」技能帮我创建/修改专家：」已经点名了技能，ChatInput
 * 只把该技能记为预选，发送时随 skills 字段交给后端激活。
 */
function writeExpertDraft(text, skills) {
  const list = (Array.isArray(skills) ? skills : [skills]).filter(Boolean)
  try {
    sessionStorage.setItem(DRAFT_KEY, text)
    const payload = {
      skills: list.map((skill) => ({
        dir_name: skill?.dir_name || skill?.name || CREATE_EXPERT_SKILL_DIR,
        name: skill?.name || skill?.dir_name || CREATE_EXPERT_SKILL_DIR,
        icon: skill?.icon || '',
        category_id: skill?.category_id || 'default',
        insert_token: false,
      })),
    }
    sessionStorage.setItem(PENDING_SKILL_KEY, JSON.stringify(payload))
  } catch (_) { /* 隐私模式等场景忽略 */ }
}

/** 在「我的技能」里查 expert-creator；返回技能记录，未安装/接口异常返回 null */
async function findExpertCreatorSkill() {
  try {
    const data = await getUserSkills()
    return (data?.skills || []).find((s) => s.dir_name === CREATE_EXPERT_SKILL_DIR) || null
  } catch (_) {
    return null // 按「未安装」处理，安装失败也不阻断创建流程
  }
}

/**
 * 确保 expert-creator 技能已添加到「我的技能」，并返回该技能记录。
 *
 * 与「创建技能」一致：未安装时自动安装（不然 Agent 拿不到这套创建规范），
 * 已安装则直接复用；409（已添加）忽略。返回的技能记录用于拼待选标签。
 *
 * 注：已添加但被**停用**的技能不在这里重新启用——停用态 Agent 确实不加载，
 * 但重复安装会 409，故交由用户在技能 Tab 里恢复。
 */
async function ensureExpertCreatorSkill() {
  let skill = await findExpertCreatorSkill()
  if (skill) return skill
  try {
    await addSkillToUser(CREATE_EXPERT_SKILL_DIR)
  } catch (e) {
    if (!String(e?.message || '').includes('已添加')) {
      showToast(e.message || '准备创建专家失败', 'error')
    }
  }
  // 安装成功后再取一次，拿到真实显示名；取不到就让 name 回退成目录名
  skill = await findExpertCreatorSkill()
  return skill
}

/** 「新建专家」：预填创建模板 → 交回 App 跳到新对话 */
async function handleCreateExpert() {
  if (preparing.value) return
  preparing.value = true
  try {
    const skill = await ensureExpertCreatorSkill()
    writeExpertDraft(CREATE_LEAD + '\n' + CREATE_TEMPLATE.replace('{name}', 'XX'), [skill])
    emit('create-expert')
  } finally {
    preparing.value = false
  }
}

/**
 * 「编辑」：预填修改模板（不含专家 ID），并把 ID 写入 sessionStorage 供请求
 * 结构化字段携带——专家 ID 不进消息正文（可被用户任意修改），由系统绑定到会话。
 */
async function handleEditExpert(item) {
  if (preparing.value || !item) return
  preparing.value = true
  try {
    const skill = await ensureExpertCreatorSkill()
    // 编辑时的技能包：expert-creator（造专家用，不落库）+ 该专家已绑定的技能包，
    // 都以「已选胶囊」形式回填——用户可增删，发送时随 skills 字段交给后端，
    // 保存时由 save_expert 写入 export_required_skills。
    const bound = requiredSkillsOf(item).map((dir) => ({ dir_name: dir, name: dir }))
    writeExpertDraft(buildEditTemplate(item), [skill, ...bound])
    // 待修改专家绑定：ID 不进消息正文，由 ChatInput 消费后随请求的
    // pending_expert_id 字段上行（后端做归属校验，只接受本人创建的专家）
    try {
      sessionStorage.setItem(
        PENDING_EXPERT_EDIT_KEY,
        JSON.stringify({
          expert_agent_id: item.export_agent_id || '',
          export_name_cn: String(item.export_name_cn || '').trim(),
        })
      )
    } catch (_) { /* 隐私模式等场景忽略 */ }
    emit('edit-expert')
  } finally {
    preparing.value = false
  }
}

/** 修改模板：把"要改什么"留给用户补，其余参数一次性给足 Agent */
function buildEditTemplate(item) {
  const name = String(item.export_name_cn || '').trim()
  const tags = skillTagsOf(item).join('、')
  const packages = requiredSkillsOf(item).join('、')
  const desc = String(item.export_description || '').trim() || '（暂无）'
  // 注意：**不要把专家 ID 写进模板**——ID 由系统经 pending_expert_id 字段绑定到
  // 会话（见 handleEditExpert），消息体里出现 ID 可被用户任意修改。
  return (
    EDIT_LEAD + '\n' +
    `【专家名称】${name}\n` +
    `【现有功能描述】${desc}\n` +
    `【现有技能标签】${tags || '（暂无）'}\n` +
    `【现有绑定技能包】${packages || '（未绑定 → 技能库全量可见）'}\n` +
    `【原始需求】${clip(item.export_prompt, 800) || '（未记录）'}\n` +
    '\n【我要修改的地方】请在这里补充要调整的内容（例如：更侧重城投债、输出必须标注数据来源）\n' +
    '（如需调整技能包，直接在输入框上方的技能胶囊里增删即可）'
  )
}

/** 长文本截断（原始需求可能很长，避免输入框被一次性灌满） */
function clip(text, max) {
  const value = String(text || '').trim()
  return value.length > max ? `${value.slice(0, max)}…` : value
}

// 当前视图：public=公共专家（预置，默认）/ mine=我的专家（自己创建的，纯列表）
const view = ref('public')
/** 公共（预置）专家：来自导出目录，只读 */
const publicExperts = ref([])
/** 我的专家：来自数据库，可编辑/删除 */
const myExperts = ref([])
/** 当前视图的数据源（模板与下方所有过滤逻辑统一读它） */
const experts = computed(() => (view.value === 'public' ? publicExperts.value : myExperts.value))
/** 分类标签候选（后端下发的 tags，来自 config.yaml 的 expert_tags；两视图共用） */
const tagOptions = ref([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
/** 当前选中的分类 tag_id（'all' = 全部）；按 tag 过滤，不再按状态过滤（仅公共专家视图） */
const tagFilter = ref('all')

const toast = ref({ show: false, message: '', type: 'success' })
/** 详情弹窗当前展示的专家（null = 关闭） */
const detailItem = ref(null)
/** 详情弹窗当前标签页：info=概要信息（默认）/ md=专家逻辑定义 / prompt=定义提示词 */
const detailTab = ref('info')
/** 详情弹窗的专家提示词 MD（只读，按 expert_id 现取） */
const detailMd = ref({ loading: false, content: '', fileName: '', error: '' })
/** MD 是否以「原文」形式展示（默认渲染后阅读） */
const detailMdRaw = ref(false)

// KaTeX / emoji 扩展注册（幂等）：专家 MD 里的公式与短代码要能正常渲染
setupMarkedExtensions()

/**
 * 专家提示词 MD 的渲染器：代码块只保留 ``<pre class="hljs">``，不套「语言 + 复制」头
 * ——详情弹窗是只读速览，带上复制按钮反而像可编辑区域（与技能详情保持一致）。
 */
const expertMdRenderer = createMarkdownRenderer({ externalLinks: false })
expertMdRenderer.code = function (token) {
  const code = typeof token === 'object' ? (token.text || token.raw || '') : String(token || '')
  return `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`
}

/** 专家提示词 MD 的渲染结果（详情弹窗「专家逻辑定义」标签页） */
/**
 * 去掉 MD 头部的 YAML frontmatter（``---`` 包起来的参数头）。
 * 它是发行包元数据，直接交给 marked 会被尾部 ``---`` 当成 setext 标题下划线，
 * 整块渲染成一个超大粗标题（比弹窗标题还大）。原文视图仍完整展示。
 */
function stripFrontmatter(md) {
  return String(md || '').replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/, '')
}

const detailMdHtml = computed(() => {
  const content = stripFrontmatter(detailMd.value.content)
  if (!content.trim()) return ''
  return renderMarkdown(content, expertMdRenderer)
})

/** 「定义提示词」只对本人创建的专家有意义；公共预置专家由发行包提供，不展示该 tab */
const showPromptTab = computed(() => !!detailItem.value && !isPublic(detailItem.value))

/** chips 数据源：后端下发的标签候选；缺失时回退为「从现有专家里聚合」 */
const tagChips = computed(() => {
  if (tagOptions.value.length) return tagOptions.value
  const seen = new Map()
  for (const item of experts.value) {
    const id = String(item.tag_id || '').trim()
    if (id && !seen.has(id)) seen.set(id, { tag_id: id, tag_name: id })
  }
  return [...seen.values()]
})

/** 每个分类下的专家数量（用于 chip 上的计数） */
const tagCounts = computed(() => {
  const counts = {}
  for (const item of experts.value) {
    const id = String(item.tag_id || '').trim()
    if (id) counts[id] = (counts[id] || 0) + 1
  }
  return counts
})

/** tag_id -> tag_name（卡片底部的分类展示用） */
function tagNameOf(tagId) {
  const id = String(tagId || '').trim()
  if (!id) return ''
  const hit = tagChips.value.find((t) => t.tag_id === id)
  return hit ? hit.tag_name : id
}

const filteredExperts = computed(() => {
  // 我的专家视图：纯列表，不做关键词/分类过滤（需求）
  if (view.value !== 'public') return experts.value

  const kw = keyword.value.trim().toLowerCase()
  return experts.value.filter((item) => {
    if (tagFilter.value !== 'all') {
      if (String(item.tag_id || '').trim() !== tagFilter.value) return false
    }
    if (!kw) return true
    return (
      (item.export_name_cn || '').toLowerCase().includes(kw) ||
      (item.export_description || '').toLowerCase().includes(kw) ||
      (item.export_prompt || '').toLowerCase().includes(kw) ||
      (item.export_name_en || '').toLowerCase().includes(kw) ||
      tagNameOf(item.tag_id).toLowerCase().includes(kw) ||
      skillTagsOf(item).join(' ').toLowerCase().includes(kw)
    )
  })
})

const emptyText = computed(() => {
  if (view.value === 'mine') return '你还没有创建专家，点上方「返回公共专家」后，在公共视图右上角「新建专家」创建一个吧'
  if (keyword.value.trim()) return `没有匹配「${keyword.value.trim()}」的专家`
  if (tagFilter.value !== 'all') {
    return `「${tagNameOf(tagFilter.value)}」分类下暂无专家`
  }
  return '公共专家目录为空，可在 experts/ 目录下新增预置专家'
})

/** 公共（预置）专家：只读（无编辑/删除入口） */
function isPublic(item) {
  return item?.scope === 'public'
}

function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value = { ...toast.value, show: false } }, 2600)
}

function avatarChar(item) {
  return expertAvatarChar(item)
}

/**
 * 专家头像配色（纯展示）：按 export_agent_id 稳定取档（全局 8 色板 .ea-0 ~ .ea-7）。
 *
 * 早期是按网格序号取色，好处是相邻卡片必然不同色，但同一个专家会随「公共/我的」
 * 视图切换、搜索过滤、加载更多而变色，也无法与消息标识、下拉列表、输入框胶囊对齐。
 * 现统一按身份取色：四处（技能中心卡片/详情、+ 号下拉、胶囊、回答面板标识）同色，
 * 刷新/换设备都不变。见 utils/expertAvatar.js。
 */
function avatarColorClass(item) {
  return expertAvatarClass(item)
}

/** 卡片描述：优先 export_description（大模型生成的功能描述），回退专家描述 */
function cardDesc(item) {
  return (
    String(item?.export_description || '').replace(/\s*\n+\s*/g, ' ').trim() ||
    String(item?.export_prompt || '').trim() ||
    '暂无描述'
  )
}

/**
 * 详情页描述：若内容本身是「一条一条」的，则拆成多行渲染（需求）。
 *
 * 拆分顺序：显式换行 → 序号标记（1. / 1、/ (1) / ① / 一、）→ 单段兜底。
 * 只有拆出 2 段以上才分行，避免把正常段落里的「1 个」误拆。
 */
function descriptionLines(item) {
  const raw = String(item?.export_description || item?.export_prompt || '').trim()
  if (!raw) return ['暂无描述']

  const byLine = raw.split(/\r?\n+/).map((s) => s.trim()).filter(Boolean)
  if (byLine.length > 1) return byLine

  const marked = raw
    .replace(/(?=(?:^|\s)\d{1,2}\s*[.、)）]\s*\S)/g, '\n')
    .replace(/(?=[①②③④⑤⑥⑦⑧⑨⑩])/g, '\n')
    .replace(/(?=(?:^|\s)[一二三四五六七八九十]{1,2}[、.]\s*\S)/g, '\n')
  const parts = marked.split('\n').map((s) => s.trim()).filter(Boolean)
  return parts.length > 1 ? parts : [raw]
}

/** 技能标签：export_skills_names（兼容旧字段 export_skills_ids 的遗留数据） */
function skillTagsOf(item) {
  const raw = item?.export_skills_names ?? item?.export_skills_ids
  if (Array.isArray(raw)) return raw.map((t) => String(t).trim()).filter(Boolean)
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.map((t) => String(t).trim()).filter(Boolean)
    } catch (_) { /* 不是 JSON，按单值处理 */ }
    return raw.split(/[,，;；\s]+/).filter(Boolean)
  }
  return []
}

/** 绑定技能包：export_required_skills（真实技能目录名，对话时的技能作用域） */
function requiredSkillsOf(item) {
  const raw = item?.export_required_skills
  if (Array.isArray(raw)) return raw.map((n) => String(n).trim()).filter(Boolean)
  if (typeof raw === 'string' && raw.trim()) {
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed.map((n) => String(n).trim()).filter(Boolean)
    } catch (_) { /* 旧数据不是 JSON，按单值处理 */ }
    return [raw.trim()]
  }
  return []
}

/** features_prompt（「专家帮你做」示例问题）规整 */
function featuresOf(item) {
  const raw = item?.export_features_prompt
  if (Array.isArray(raw)) return raw.map((f) => String(f).trim()).filter(Boolean)
  return []
}

/** 详情页版本号：整数版本 -> v1.0.0 风格展示 */
function detailVersion(item) {
  const v = parseInt(item?.export_version ?? 1, 10)
  return `${Number.isFinite(v) ? v : 1}.0.0`
}

/**
 * 打开专家详情：默认落在「专家设定 MD」页，同时按需拉取提示词正文。
 * 详情弹窗是只读视图——MD 与提示词都没有编辑入口。
 */
function openDetail(item) {
  detailItem.value = item
  detailTab.value = 'info'
  detailMdRaw.value = false
  loadDetailMd(item)
}

/** 拉取该专家的提示词 MD（我的专家取 active 版本，公共专家取预置文件） */
async function loadDetailMd(item) {
  const expertId = item && item.export_agent_id
  if (!expertId) return
  detailMd.value = { loading: true, content: '', fileName: '', error: '' }
  try {
    const data = await getExpertMd(expertId)
    // 弹窗已切到别的专家：丢弃旧响应
    if (!detailItem.value || detailItem.value.export_agent_id !== expertId) return
    detailMd.value = {
      loading: false,
      content: (data && data.content) || '',
      fileName: (data && data.file_name) || '',
      error: '',
    }
  } catch (e) {
    if (!detailItem.value || detailItem.value.export_agent_id !== expertId) return
    detailMd.value = {
      loading: false,
      content: '',
      fileName: '',
      error: (e && e.message) || '获取专家提示词失败',
    }
  }
}

function closeDetail() {
  detailItem.value = null
  detailMd.value = { loading: false, content: '', fileName: '', error: '' }
  detailMdRaw.value = false
}

/** 「召唤」：把专家挂到当前会话（同原「使用」，仅文案变化） */
function summon(item) {
  emit('use', item)
  closeDetail()
}

/** 点示例问题：召唤专家并回填输入框（由 App.vue 落到 ChatInput） */
function summonWithPrompt(item, prompt) {
  emit('use', item, prompt)
  closeDetail()
}

function formatTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const diff = date.getTime() - Date.now()
  const day = 86400000
  const hour = 3600000
  const minute = 60000
  const rtf = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })
  if (Math.abs(diff) >= day && Math.abs(diff) <= 30 * day) return rtf.format(Math.round(diff / day), 'day')
  if (Math.abs(diff) >= hour && Math.abs(diff) < day) return rtf.format(Math.round(diff / hour), 'hour')
  if (Math.abs(diff) < hour) return rtf.format(Math.round(diff / minute), 'minute')
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(date)
}

/**
 * 拉取当前视图的数据。
 *
 * - public：公共（预置）专家目录，只读；
 * - mine：我的专家（数据库），已删除（软删）的专家不再展示。
 *
 * 分类标签候选（tags）两个接口都会下发，取到即缓存，供卡片底部的 tag_name 展示。
 */
async function loadExperts(scope) {
  if (scope === 'mine') {
    const data = await listExperts({ includeDisabled: false })
    myExperts.value = data.items || []
    if (!tagOptions.value.length && Array.isArray(data.tags)) tagOptions.value = data.tags
    return
  }
  const data = await listPublicExperts()
  publicExperts.value = data.items || []
  if (Array.isArray(data.tags) && data.tags.length) tagOptions.value = data.tags
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await loadExperts(view.value)
    if (
      view.value === 'public' &&
      tagFilter.value !== 'all' &&
      !tagOptions.value.some((t) => t.tag_id === tagFilter.value)
    ) {
      // 选中的分类在下发列表里已不存在（配置调整）→ 回到「全部」
      tagFilter.value = 'all'
    }
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshSilently(scope = view.value) {
  try {
    await loadExperts(scope)
  } catch (_) { /* 静默失败，保留旧数据 */ }
}

/** 切换视图：我的专家视图不带搜索与分类筛选，切换时把过滤条件复位 */
function switchView(next) {
  if (view.value === next) return
  view.value = next
  keyword.value = ''
  tagFilter.value = 'all'
  refresh()
}

function clearKeyword() {
  keyword.value = ''
  refresh()
}

async function confirmDelete(item) {
  const ok = window.confirm(
    `确定删除专家「${item.export_name_cn}」吗？`
  )
  if (!ok) return
  try {
    await deleteExpert(item.export_agent_id, true)
    await refreshSilently('mine')
    showToast(`已删除「${item.export_name_cn}」`)
  } catch (e) {
    showToast(e.message || '删除失败', 'error')
  }
}

onMounted(refresh)
</script>

<style scoped>
/* 与 SkillListPanel 同构：占满 Tab 内容区，内部自己滚动 */
.expert-panel {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;

  /* ── 专家卡片配色（对齐参考示例：浅色系 / 白卡浅灰描边）──────────
     白底卡片 + 深色标题 + 灰色描述 + 浅灰标签 chips + 浅色底栏，
     与「专家广场」示例一致；深色页面由文件末尾 html[data-theme="dark"]
     覆写为深炭卡。所有卡片色值集中在这里 —— 后续调色只需改这一块。 */
  --xcard-bg: #ffffff;
  --xcard-border: #e5e6eb;
  --xcard-border-hover: #c9cdd4;
  --xcard-inset: 0 1px 2px rgba(31, 35, 41, 0.04);
  --xcard-shadow-hover: 0 10px 26px rgba(31, 35, 41, 0.1);
  --xcard-avatar-bg: linear-gradient(150deg, #eaf4ff 0%, #dcebff 100%);
  --xcard-avatar-text: var(--accent-color);
  --xcard-title: #1f2329;
  --xcard-desc: #86909c;
  --xcard-tag-bg: #f2f3f5;
  --xcard-tag-border: transparent;
  --xcard-tag-text: #4e5969;
  --xcard-footer-bg: transparent;
  --xcard-footer-border: #f2f3f5;
  --xcard-footer-text: #86909c;
  --xcard-action: #4e5969;
  --xcard-action-hover-bg: #f2f3f5;
  --xcard-action-hover-text: #1f2329;
  --xcard-danger: #f53f3f;
  --xcard-primary-bg: var(--accent-color);
  --xcard-primary-text: #ffffff;
}

/* ── 工具条 ── */
.expert-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 16px 24px 0;
}

.search-box {
  flex: 1;
  min-width: 160px;
  max-width: 320px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.search-box:focus-within {
  border-color: var(--accent-color);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color) 15%, transparent);
}

.search-box > svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.search-box input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
}

.search-box input::placeholder {
  color: var(--text-secondary);
}

.search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  cursor: pointer;
}

.search-clear svg {
  width: 12px;
  height: 12px;
}

.search-clear:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 1px;
}

.action-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  padding: 8px 14px;
  border: 1px solid var(--accent-color);
  border-radius: 8px;
  background: var(--accent-color);
  color: #fff;
  font-size: 13px;
  /* 防止按钮文案在窄容器里折行（折行会导致按钮高度不一致） */
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

/* 「我的专家」入口：与「新建专家」并排紧挨（间距 8px），
   其余配色 / 高度 / 字号完全复用 .action-btn-primary */
.action-btn-primary.mine-btn {
  margin-left: 8px;
}

.action-btn-primary:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent-color) 84%, #000);
}

.action-btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn-primary:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.action-btn-primary svg {
  width: 14px;
  height: 14px;
}

.action-btn-outline {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
}

.action-btn-outline:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

/* ── 「我的专家」视图：返回公共专家的入口（顶替搜索框的占位）── */
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: auto;
  padding: 7px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.back-btn:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

.back-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.back-btn svg {
  width: 15px;
  height: 15px;
}

/* ── 「我的专家」视图的说明条（顶替 chips 行，保持两视图纵向节奏一致）── */
.view-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 24px 0;
}

.view-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.view-tip {
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* ── 状态 chips ── */
.status-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 24px 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.chip:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.chip.active {
  border-color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
}

.chip:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.chip-count {
  font-size: 11.5px;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
}

/* ── 内容区 ── */
.expert-center-content {
  min-width: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px 24px 24px;
  position: relative;
}

.state-box {
  height: 100%;
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
}

.state-box svg {
  width: 44px;
  height: 44px;
}

.state-box p {
  margin: 0;
}

/* 三列栅格：固定一行 3 张。
   单卡宽度 = (内容区宽 − 左右内边距 24px×2 − 列间距 16px×2) / 3：
     · 1440px 视口（侧栏 260）→ 内容区 ≈1132px → 单卡 ≈366px
     · 1920px 视口            → 内容区 ≈1612px → 单卡 ≈526px
   窄屏逐级降为 2 列 / 1 列（见文件末尾媒体查询）。 */
.experts-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  /* 行高下限 184px：需求下限 180px，取 184px 作为 180~190 区间的最优值
     —— 内容自然高度 ≈183px（2 行描述 + 技能标签行 + 底栏），
        既不贴到 180 下限，也远离 190 上限，留出字体差异余量。 */
  grid-auto-rows: minmax(184px, auto);
}

.expert-card {
  display: flex;
  flex-direction: column;
  min-height: 184px;
  border: 1px solid var(--xcard-border);
  border-radius: 12px;
  background: var(--xcard-bg);
  box-shadow: var(--xcard-inset);
  color: var(--xcard-title);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.expert-card:hover {
  transform: translateY(-2px);
  border-color: var(--xcard-border-hover);
  box-shadow: var(--xcard-inset), var(--xcard-shadow-hover);
}

.expert-card:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.expert-card-inner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 16px 14px;
}

.expert-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 11px;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 提供（按 export_agent_id 稳定取档）；
     此处不写死，避免 scoped 规则与全局色板同权重竞争后按注入顺序覆盖配色。 */
  font-size: 16px;
  font-weight: 600;
}

/* 头像配色统一用全局 .ea-0 ~ .ea-7（style.css + utils/expertAvatar.js）：
   按 export_agent_id 稳定取档，与 + 号下拉 / 胶囊 / 回答面板标识同色。
   不要再按网格序号另造一套色板。 */

.expert-card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.expert-card-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  line-height: 1.45;
}

.expert-card-name {
  font-size: 14.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--xcard-title);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-tag {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
  color: var(--accent-color);
  font-size: 11px;
  line-height: 1.5;
}

.expert-tag.warn {
  background: rgba(245, 158, 11, 0.14);
  color: #d97706;
}

.expert-tag.off {
  background: var(--xcard-tag-bg);
  color: #a9aeb8;
}

/* 描述固定 2 行（min-height 保证短文案也占满 2 行，卡片高度才整齐） */
.expert-card-desc {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--xcard-desc);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  min-height: calc(2 * 1.6em);
}

/* 主操作：强调色实底 + 白字，浅色卡面上的视觉锚点 */
.use-btn {
  flex-shrink: 0;
  padding: 5px 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--xcard-primary-bg);
  color: var(--xcard-primary-text);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.use-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--xcard-primary-bg) 86%, #000);
}

.use-btn:disabled {
  background: var(--xcard-tag-bg);
  border-color: transparent;
  color: #a9aeb8;
  cursor: not-allowed;
}

.use-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.expert-card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  /* 出现技能标签行时把底栏顶下去，没有标签行的卡片底栏也贴底，卡片高度才一致 */
  margin-top: auto;
  padding: 11px 16px;
  border-top: 1px solid var(--xcard-footer-border);
  background: var(--xcard-footer-bg);
  font-size: 11.5px;
  color: var(--xcard-footer-text);
  font-variant-numeric: tabular-nums;
}

.expert-card-footer .dot {
  opacity: 0.5;
}

.footer-tag {
  max-width: 120px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.footer-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.footer-btn {
  padding: 2px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--xcard-action);
  font-size: 11.5px;
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease;
}

.footer-btn:hover:not(:disabled) {
  background: var(--xcard-action-hover-bg);
  color: var(--xcard-action-hover-text);
}

.footer-btn.danger:hover:not(:disabled) {
  background: rgba(248, 113, 113, 0.16);
  color: var(--xcard-danger);
}

.footer-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.footer-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 1px;
}

/* ── 创建 / 编辑 弹窗 ── */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.modal {
  width: min(560px, 92vw);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  border-radius: 14px;
  overflow: hidden;
}

.modal-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 6px;
  cursor: pointer;
}

.modal-close svg {
  width: 18px;
  height: 18px;
}

.modal-close:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

/* ── 卡片技能标签（export_skills_names）──
   基础样式仍供浅色容器（详情弹窗 .detail-tags）复用；
   卡片内覆写为深色卡面上的描边标签。 */
.card-skill-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  /* 不换行 + 溢出隐藏：标签再多也撑不高卡片，保证一行内三张卡等高 */
  flex-wrap: nowrap;
  overflow: hidden;
  padding: 0 16px 14px;
}

.skill-tag {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-skill-tags .skill-tag {
  flex: 0 1 auto;
  min-width: 0;
  padding: 3px 9px;
  border: 1px solid var(--xcard-tag-border);
  background: var(--xcard-tag-bg);
  color: var(--xcard-tag-text);
  line-height: 1.4;
}

.card-skill-tags .skill-tag.more {
  color: var(--accent-color);
  border-color: transparent;
}

/* ── 专家详情弹窗 ── */
.detail-modal {
  position: relative;
  /* 详情需要读整份专家提示词 MD：宽版（960px），窄屏以 96vw 封顶避免溢出 */
  width: 960px;
  min-width: min(960px, 96vw);
  max-width: 96vw;
}

.detail-close {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 1;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px 20px 0;
}

.detail-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  flex-shrink: 0;
  border-radius: 14px;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 提供，与卡片头像、消息标识同色 */
  font-size: 20px;
  font-weight: 700;
}

.detail-head-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding-right: 24px;
}

/* 召唤按钮靠右，与标题行同一水平线（宽版弹窗空间足够） */
.detail-head-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  padding-right: 24px;
}

.detail-head-tags {
  margin-top: 2px;
}

/* ── 详情标签页（设定 MD / 定义提示词 / 概要） ── */
.detail-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 12px 20px 0;
  border-bottom: 1px solid var(--border-color);
}

.detail-tab {
  padding: 8px 14px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.detail-tab:hover {
  color: var(--text-primary);
}

.detail-tab.active {
  color: var(--text-primary);
  font-weight: 600;
  border-bottom-color: var(--text-primary);
}

.detail-tabs-hint {
  margin-left: auto;
  padding-bottom: 6px;
  font-size: 11px;
  color: var(--text-secondary);
}

.detail-name-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.detail-name {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-primary);
}

.detail-name-en {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summon-btn {
  padding: 6px 18px;
  border: none;
  border-radius: 8px;
  background: var(--text-primary);
  color: var(--bg-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.18s ease;
}

.summon-btn:hover:not(:disabled) {
  opacity: 0.85;
}

.summon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.summon-btn:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

.detail-body {
  padding: 14px 20px 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ── 专家设定 MD（只读渲染 / 原文切换） ── */
.detail-md-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.detail-md-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.detail-md-file {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
  border-radius: 6px;
  padding: 3px 8px;
}

.detail-md-raw-btn {
  border: none;
  background: transparent;
  color: var(--accent-color);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  padding: 2px 4px;
}

.detail-md-state {
  padding: 28px 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
}

.detail-md-state.is-error {
  color: #d4380d;
}

.detail-md-code,
.detail-prompt-text {
  margin: 0;
  max-height: 52vh;
  overflow: auto;
  padding: 14px 16px;
  background: var(--bg-tertiary);
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
}

.detail-md-render {
  max-height: 52vh;
  overflow: auto;
  padding: 16px 18px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  /* 阅读区字号小于弹窗正文（13.5px）：MD 自带标题层级，整体再小一档才不喧宾夺主 */
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--text-primary);
}

/* v-html 内容不受 scoped 约束，必须用深度选择器 */
.detail-md-render :deep(h1),
.detail-md-render :deep(h2),
.detail-md-render :deep(h3),
.detail-md-render :deep(h4) {
  margin: 14px 0 6px;
  font-weight: 600;
  line-height: 1.4;
}

/* 标题字号必须小于弹窗标题（17px）与 tab（13px 加粗），否则「专家名」反而比正文标题小 */
.detail-md-render :deep(h1) { font-size: 14.5px; }
.detail-md-render :deep(h2) { font-size: 13.5px; }
.detail-md-render :deep(h3) { font-size: 13px; }
.detail-md-render :deep(h4) { font-size: 12.5px; }

.detail-md-render :deep(h1:first-child),
.detail-md-render :deep(h2:first-child),
.detail-md-render :deep(h3:first-child) {
  margin-top: 0;
}

.detail-md-render :deep(p),
.detail-md-render :deep(ul),
.detail-md-render :deep(ol),
.detail-md-render :deep(blockquote),
.detail-md-render :deep(table) {
  margin: 0 0 10px;
}

.detail-md-render :deep(ul),
.detail-md-render :deep(ol) {
  padding-left: 22px;
}

.detail-md-render :deep(li) {
  margin: 3px 0;
}

.detail-md-render :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  background: var(--bg-tertiary);
  border-radius: 4px;
  padding: 1px 5px;
}

.detail-md-render :deep(pre) {
  margin: 0 0 10px;
  padding: 12px 14px;
  background: var(--bg-tertiary);
  border-radius: 8px;
  overflow: auto;
}

.detail-md-render :deep(pre code) {
  background: transparent;
  padding: 0;
}

.detail-md-render :deep(blockquote) {
  padding: 6px 12px;
  border-left: 3px solid var(--border-color);
  color: var(--text-secondary);
}

.detail-md-render :deep(table) {
  border-collapse: collapse;
  width: 100%;
}

.detail-md-render :deep(th),
.detail-md-render :deep(td) {
  border: 1px solid var(--border-color);
  padding: 6px 10px;
  font-size: 12.5px;
}

.detail-md-render :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-color);
  margin: 14px 0;
}

.detail-md-render :deep(a) {
  color: var(--accent-color);
}

/* ── 定义提示词（export_prompt 原文） ── */
.detail-prompt-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-pane-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.detail-pane-empty {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

/* 概要信息各区段（描述 / 绑定技能包 / 专家帮你做 / 版本）之间留出呼吸感，
   原先靠各段自带的 8px margin 拼凑，第一段描述几乎贴着「绑定技能包」标题 */
.detail-info-pane {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.detail-desc-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-desc {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.detail-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 绑定技能包里的技能名：用真实目录名，比卡片上的短标签更长，字号随即放大一档 */
.detail-skill-list .skill-tag {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12.5px;
  max-width: 220px;
  color: var(--text-primary);
}

/* 技能包说明：纯文字小注（此前与技能 chip 同为灰底块，视觉上和列表混成一片） */
.detail-skills-hint {
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
}

.detail-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--text-primary);
}

.section-icon {
  font-size: 14px;
  font-style: normal;
}

.feature-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 8px;
  border: none;
  border-radius: 10px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.18s ease;
}

.feature-row:hover {
  background: color-mix(in srgb, var(--accent-color) 10%, var(--bg-tertiary));
}

.feature-row:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 1px;
}

.feature-text {
  flex: 1;
  min-width: 0;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.feature-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  color: var(--text-secondary);
}

.more-row {
  padding: 10px 14px;
  margin-bottom: 8px;
  border-radius: 10px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  font-size: 12.5px;
  overflow-wrap: anywhere;
}

/* ── Toast（同技能 Tab）── */
.toast {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  padding: 9px 16px;
  border-radius: 8px;
  font-size: 13px;
  color: #fff;
  background: #1e293b;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.28);
  z-index: 20;
}

.toast.error {
  background: #b91c1c;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}

.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: expert-spin 0.8s linear infinite;
}

@keyframes expert-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
  }

  .toast-enter-active,
  .toast-leave-active {
    transition: none;
  }
}

@media (max-width: 48rem) {
  .expert-toolbar,
  .status-chips,
  .expert-center-content {
    padding-left: 16px;
    padding-right: 16px;
  }

  .search-box {
    max-width: none;
  }

  .action-btn-primary {
    margin-left: 0;
  }

  /* 窄屏下两按钮换行排列，保持 8px 间距 */
  .action-btn-primary.mine-btn {
    margin-left: 8px;
  }
}

/* 深色主题：白卡整体翻为深炭卡，保证在纯黑背景上仍有层次 */
html[data-theme="dark"] .expert-panel {
  --xcard-bg: linear-gradient(158deg, #2a2b31 0%, #1d1e22 100%);
  --xcard-border: rgba(255, 255, 255, 0.13);
  --xcard-border-hover: rgba(255, 255, 255, 0.3);
  --xcard-inset: inset 0 1px 0 rgba(255, 255, 255, 0.09);
  --xcard-shadow-hover: 0 14px 30px rgba(0, 0, 0, 0.5);
  --xcard-avatar-bg: linear-gradient(150deg, #5b5b66 0%, #26262c 100%);
  --xcard-avatar-text: #fafafa;
  --xcard-title: #fafafa;
  --xcard-desc: #a1a1aa;
  --xcard-tag-bg: rgba(255, 255, 255, 0.07);
  --xcard-tag-border: rgba(255, 255, 255, 0.13);
  --xcard-tag-text: #d4d4d8;
  --xcard-footer-bg: rgba(0, 0, 0, 0.25);
  --xcard-footer-border: rgba(255, 255, 255, 0.1);
  --xcard-footer-text: #90909a;
  --xcard-action: #b4b4bd;
  --xcard-action-hover-bg: rgba(255, 255, 255, 0.12);
  --xcard-action-hover-text: #fafafa;
  --xcard-danger: #f87171;
  --xcard-primary-bg: #fafafa;
  --xcard-primary-text: #18181b;
}

/* 窄屏降级：3 列 → 2 列 → 1 列
   按「单卡最小可用宽度 280px + 内容区左右 24px + 列间距」反推视口阈值 */
@media (max-width: 1180px) {
  .experts-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .experts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
/* ── 旧浏览器兜底：color-mix() 需要 Chromium 111+ ─────────────────────
   Edge 92 / Chrome ≤ 110 会把 `background: color-mix(...)` 整条丢弃，hover 时
   底色退回透明、文字仍是 #fff，按钮看起来"变白消失"（新建专家 / 我的专家 / 召唤）。
   这里用「基色 + 叠一层黑」复现 color-mix(基色 84%~86%, #000)：sRGB 下叠 α 黑
   与 color-mix(基色 (1-α), #000) 完全等价，所以浅色/暗色主题都不用分别写死颜色。
   新浏览器 @supports 判定为真，整块跳过，视觉零变化。 */
@supports not (background: color-mix(in srgb, #000 10%, #fff)) {
  /* 主色按钮（新建专家 / 我的专家）：复现 color-mix(主色 84%, #000) */
  .action-btn-primary:hover:not(:disabled) {
    background-color: var(--accent-color);
    background-image: linear-gradient(rgba(0, 0, 0, 0.16), rgba(0, 0, 0, 0.16));
  }

  /* 召唤按钮：复现 color-mix(--xcard-primary-bg 86%, #000)；
     该基色在暗色主题下接近白色，所以不能用主色兜底 */
  .use-btn:hover:not(:disabled) {
    background-color: var(--xcard-primary-bg);
    background-image: linear-gradient(rgba(0, 0, 0, 0.14), rgba(0, 0, 0, 0.14));
  }

  /* 分类 chip 选中态同样用了 color-mix(主色 12%, transparent)，失效后底色全无 */
  .chip.active {
    background-color: var(--bg-tertiary);
  }
}

</style>
