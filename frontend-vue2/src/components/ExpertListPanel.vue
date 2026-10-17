<!--
  专家列表（「专家 · 技能 · 连接器」容器的专家 Tab 主视图）—— Vue2 / Vue CLI 版。
  与 frontend/（Vue3 + vite）的 ExpertListPanel.vue 保持同一套模板结构与样式。

  两个视图（面板内切换，不改动 SkillCenter.vue）：
  - **公共专家（默认）**：预置专家库，来自后端 exports 目录（GET /agent/public-list）。
    保留完整的浏览体验：搜索 + 分类 chips（tag_name 展示、tag_id 过滤）。
    卡片只读：无「编辑 / 删除」，状态位显示「预置」。
  - **我的专家**：自己创建的专家（GET /agent/list），**仅列表**——无搜索、无分类筛选，
    卡片编辑/删除可用。入口是工具条「新建专家」右侧的「我的专家」按钮。

  样式与交互对齐 SkillListPanel.vue（技能 Tab）：
  - 工具条：搜索 / 新建专家 / 我的专家
  - 分类 chips：全部 + 配置里的标签（tag_name 展示、tag_id 过滤，见 /agent/list 的 tags）
  - 卡片：一行 3 张、最小高度 184px；头像 / 名称 + 状态徽标 / 功能描述
    （export_description）/ 技能标签（export_skills_names，最多 3 个）/ 底部 版本·更新时间·分类
  - 点「召唤」/ 点示例问题 → $emit('use', item, prompt?)，由 App.vue 挂载专家胶囊
    （带 prompt 时回填输入框）并选中该专家（对话统一走 /agent/chat/stream）
  - 点卡片 → 专家详情弹窗（功能描述[逐条换行] / 技能标签 / 专家帮你做 / 版本与 ID）

  新建 / 编辑专家（与「添加技能」同一套跳转，见 SkillListPanel.handleCreateSkill）：
  - 「新建专家」→ 确保 expert-creator 技能已安装 → 草稿写入 sessionStorage
    （开头是「用「expert-creator」技能帮我创建：」，替代内联的 /expert-creator
    标签；技能以 insert_token:false 预选，发送时随 skills 字段激活）→
    $emit('create-expert')，由 App 新开会话；ChatInput 挂载时恢复草稿与预选技能；
  - 卡片「编辑」→ 同上，但预填的是**当前专家参数 + 专家 ID** 的修改模板 →
    $emit('edit-expert')，由 App 决定「当前窗口是新会话就带入、已有对话则新开会话」。
  原先的创建/编辑弹窗已移除（后端 POST /agent/add、/agent/update 接口保留）。

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
              @click.stop="$emit('use', item)"
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
            <span>v{{ versionOf(item) }}</span>
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

    <!-- 创建 / 编辑弹窗已移除：新建与编辑都改到对话框里由 Agent 完成（见 handleCreateExpert /
         handleEditExpert）。后端 POST /agent/add、POST /agent/update 接口保留，供其它调用方使用。 -->

    <!-- 专家详情弹窗：点卡片打开；右上角 × 或点击遮罩（弹窗外的灰色区域）均可关闭。 -->
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

        <div class="detail-body">
          <!-- 功能描述：若大模型给的是一条一条的，则逐行展示（见 descriptionLines） -->
          <div class="detail-desc-block">
            <p v-for="(line, idx) in descriptionLines(detailItem)" :key="idx" class="detail-desc">
              {{ line }}
            </p>
          </div>

          <div v-if="skillTagsOf(detailItem).length" class="detail-tags">
            <span v-for="tag in skillTagsOf(detailItem)" :key="tag" class="skill-tag">{{ tag }}</span>
          </div>

          <div v-if="featuresOf(detailItem).length" class="detail-section">
            <h4 class="detail-section-title">
              <span class="section-icon" aria-hidden="true">💡</span> 专家帮你做
            </h4>
            <button
              v-for="(f, idx) in featuresOf(detailItem)"
              :key="idx"
              class="feature-row"
              title="召唤专家并填入该问题"
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

    <transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
        {{ toast.message }}
      </div>
    </transition>
  </div>
</template>

<script>
import { deleteExpert, listExperts, listPublicExperts } from '../api/expert.js'
// 别名导入：模板方法同名（Vue2 模板只能访问实例成员）
import {
  expertAvatarChar as buildExpertAvatarChar,
  expertAvatarClass as buildExpertAvatarClass
} from '../utils/expertAvatar.js'
import { addSkillToUser, getUserSkills } from '../api/skills.js'

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

/** 长文本截断（原始需求可能很长，避免输入框被一次性灌满） */
function clip(text, max) {
  const value = String(text || '').trim()
  return value.length > max ? `${value.slice(0, max)}…` : value
}

export default {
  name: 'ExpertListPanel',

  props: {
    /** 当前会话 ID（保留：面板不再直接创建专家，创建走对话页的 Agent 流程） */
    sessionId: {
      type: String,
      default: ''
    }
  },

  emits: ['use', 'create-expert', 'edit-expert'],

  data() {
    return {
      //── 视图：public=公共专家（预置，默认）/ mine=我的专家（自己创建的，纯列表）──
      view: 'public',
      /** 公共（预置）专家：来自导出目录，只读 */
      publicExperts: [],
      /** 我的专家：来自数据库，可编辑/删除 */
      myExperts: [],
      /** 分类标签候选（后端下发的 tags，来自 config.yaml 的 expert_tags；两视图共用） */
      tagOptions: [],
      loading: false,
      error: '',
      keyword: '',
      /** 当前选中的分类 tag_id（'all' = 全部）；按 tag 过滤，不再按状态过滤（仅公共专家视图） */
      tagFilter: 'all',

      /** 正在准备（安装技能 / 写草稿）时禁用创建与编辑入口，避免连点 */
      preparing: false,
      toast: { show: false, message: '', type: 'success' },
      /** 详情弹窗当前展示的专家（null = 关闭） */
      detailItem: null,
      toastTimer: null,
    }
  },

  computed: {
    /** 当前视图的数据源（模板与下方所有过滤逻辑统一读它） */
    experts() {
      return this.view === 'public' ? this.publicExperts : this.myExperts
    },

    /** chips 数据源：后端下发的标签候选；缺失时回退为「从现有专家里聚合」 */
    tagChips() {
      if (this.tagOptions.length) return this.tagOptions
      const seen = {}
      const list = []
      for (const item of this.experts) {
        const id = String(item.tag_id || '').trim()
        if (id && !seen[id]) {
          seen[id] = true
          list.push({ tag_id: id, tag_name: id })
        }
      }
      return list
    },

    /** 每个分类下的专家数量（用于 chip 上的计数） */
    tagCounts() {
      const counts = {}
      for (const item of this.experts) {
        const id = String(item.tag_id || '').trim()
        if (id) counts[id] = (counts[id] || 0) + 1
      }
      return counts
    },

    filteredExperts() {
      // 我的专家视图：纯列表，不做关键词/分类过滤（需求）
      if (this.view !== 'public') return this.experts

      const kw = this.keyword.trim().toLowerCase()
      return this.experts.filter((item) => {
        if (this.tagFilter !== 'all') {
          if (String(item.tag_id || '').trim() !== this.tagFilter) return false
        }
        if (!kw) return true
        return (
          (item.export_name_cn || '').toLowerCase().includes(kw) ||
          (item.export_description || '').toLowerCase().includes(kw) ||
          (item.export_prompt || '').toLowerCase().includes(kw) ||
          (item.export_name_en || '').toLowerCase().includes(kw) ||
          this.tagNameOf(item.tag_id).toLowerCase().includes(kw) ||
          this.skillTagsOf(item).join(' ').toLowerCase().includes(kw)
        )
      })
    },

    emptyText() {
      if (this.view === 'mine') {
        return '你还没有创建专家，点上方「返回公共专家」后，在公共视图右上角「新建专家」创建一个吧'
      }
      if (this.keyword.trim()) return `没有匹配「${this.keyword.trim()}」的专家`
      if (this.tagFilter !== 'all') {
        return `「${this.tagNameOf(this.tagFilter)}」分类下暂无专家`
      }
      return '公共专家目录为空，可在 exports/ 目录下新增预置专家'
    },
  },

  mounted() {
    this.refresh()
  },

  beforeDestroy() {
    if (this.toastTimer) clearTimeout(this.toastTimer)
  },

  methods: {
    /** tag_id -> tag_name（卡片底部的分类展示用） */
    tagNameOf(tagId) {
      const id = String(tagId || '').trim()
      if (!id) return ''
      const hit = this.tagChips.find((t) => t.tag_id === id)
      return hit ? hit.tag_name : id
    },

    /** 公共（预置）专家：只读（无编辑/删除入口） */
    isPublic(item) {
      return !!item && item.scope === 'public'
    },

    showToast(message, type = 'success') {
      this.toast = { show: true, message, type }
      if (this.toastTimer) clearTimeout(this.toastTimer)
      this.toastTimer = setTimeout(() => {
        this.toast = { ...this.toast, show: false }
      }, 2600)
    },

    avatarChar(item) {
      return buildExpertAvatarChar(item)
    },

    /**
     * 专家头像配色（纯展示）：按 export_agent_id 稳定取档（全局 8 色板 .ea-0 ~ .ea-7）。
     *
     * 早期是按网格序号取色，好处是相邻卡片必然不同色，但同一个专家会随「公共/我的」
     * 视图切换、搜索过滤、加载更多而变色，也无法与消息标识、下拉列表、输入框胶囊对齐。
     * 现统一按身份取色：四处（技能中心卡片/详情、+ 号下拉、胶囊、回答面板标识）同色，
     * 刷新/换设备都不变。见 utils/expertAvatar.js。
     */
    avatarColorClass(item) {
      return buildExpertAvatarClass(item)
    },

    /** 卡片描述：优先 export_description（大模型生成的功能描述），回退专家描述 */
    cardDesc(item) {
      return (
        String((item && item.export_description) || '').replace(/\s*\n+\s*/g, ' ').trim() ||
        String((item && item.export_prompt) || '').trim() ||
        '暂无描述'
      )
    },

    /**
     * 详情页描述：若内容本身是「一条一条」的，则拆成多行渲染（需求）。
     *
     * 拆分顺序：显式换行 → 序号标记（1. / 1、/ (1) / ① / 一、）→ 单段兜底。
     * 只有拆出 2 段以上才分行，避免把正常段落里的「1 个」误拆。
     */
    descriptionLines(item) {
      const raw = String(
        (item && (item.export_description || item.export_prompt)) || ''
      ).trim()
      if (!raw) return ['暂无描述']

      const byLine = raw.split(/\r?\n+/).map((s) => s.trim()).filter(Boolean)
      if (byLine.length > 1) return byLine

      const marked = raw
        .replace(/(?=(?:^|\s)\d{1,2}\s*[.、)）]\s*\S)/g, '\n')
        .replace(/(?=[①②③④⑤⑥⑦⑧⑨⑩])/g, '\n')
        .replace(/(?=(?:^|\s)[一二三四五六七八九十]{1,2}[、.]\s*\S)/g, '\n')
      const parts = marked.split('\n').map((s) => s.trim()).filter(Boolean)
      return parts.length > 1 ? parts : [raw]
    },

    /** 技能标签：export_skills_names（兼容旧字段 export_skills_ids 的遗留数据） */
    skillTagsOf(item) {
      if (!item) return []
      const raw = item.export_skills_names != null
        ? item.export_skills_names
        : item.export_skills_ids
      if (Array.isArray(raw)) return raw.map((t) => String(t).trim()).filter(Boolean)
      if (typeof raw === 'string' && raw.trim()) {
        try {
          const parsed = JSON.parse(raw)
          if (Array.isArray(parsed)) return parsed.map((t) => String(t).trim()).filter(Boolean)
        } catch (e) {
          // 不是 JSON，按单值处理
        }
        return raw.split(/[,，;；\s]+/).filter(Boolean)
      }
      return []
    },

    /** features_prompt（「专家帮你做」示例问题）规整 */
    featuresOf(item) {
      const raw = item && item.export_features_prompt
      if (Array.isArray(raw)) return raw.map((f) => String(f).trim()).filter(Boolean)
      return []
    },

    /** 卡片版本号：缺省视为 v1 */
    versionOf(item) {
      const v = parseInt(item && item.export_version, 10)
      return Number.isFinite(v) ? v : 1
    },

    /** 详情页版本号：整数版本 -> v1.0.0 风格展示 */
    detailVersion(item) {
      return `${this.versionOf(item)}.0.0`
    },

    openDetail(item) {
      this.detailItem = item
    },

    closeDetail() {
      this.detailItem = null
    },

    /** 「召唤」：把专家挂到当前会话（同原「使用」，仅文案变化） */
    summon(item) {
      this.$emit('use', item)
      this.closeDetail()
    },

    /** 点示例问题：召唤专家并回填输入框（由 App.vue 落到 ChatInput） */
    summonWithPrompt(item, prompt) {
      this.$emit('use', item, prompt)
      this.closeDetail()
    },

    formatTime(iso) {
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
    },

    /**
     * 拉取指定视图的数据。
     *
     * - public：公共（预置）专家目录，只读；
     * - mine：我的专家（数据库），已删除（软删）的专家不再展示。
     *
     * 分类标签候选（tags）两个接口都会下发，取到即缓存，供卡片底部的 tag_name 展示。
     */
    async loadExperts(scope) {
      if (scope === 'mine') {
        const data = await listExperts({ includeDisabled: false })
        this.myExperts = data.items || []
        if (!this.tagOptions.length && Array.isArray(data.tags)) this.tagOptions = data.tags
        return
      }
      const data = await listPublicExperts()
      this.publicExperts = data.items || []
      if (Array.isArray(data.tags) && data.tags.length) this.tagOptions = data.tags
    },

    async refresh() {
      this.loading = true
      this.error = ''
      try {
        await this.loadExperts(this.view)
        if (
          this.view === 'public' &&
          this.tagFilter !== 'all' &&
          !this.tagOptions.some((t) => t.tag_id === this.tagFilter)
        ) {
          // 选中的分类在下发列表里已不存在（配置调整）→ 回到「全部」
          this.tagFilter = 'all'
        }
      } catch (e) {
        this.error = e.message || '加载失败'
      } finally {
        this.loading = false
      }
    },

    async refreshSilently(scope = this.view) {
      try {
        await this.loadExperts(scope)
      } catch (e) {
        // 静默失败，保留旧数据
      }
    },

    /** 切换视图：我的专家视图不带搜索与分类筛选，切换时把过滤条件复位 */
    switchView(next) {
      if (this.view === next) return
      this.view = next
      this.keyword = ''
      this.tagFilter = 'all'
      this.refresh()
    },

    clearKeyword() {
      this.keyword = ''
      this.refresh()
    },

    /** 把提示词模板与技能标签写入 sessionStorage（ChatInput 挂载时恢复） */
    /**
     * 把提示词模板与技能标签写入 sessionStorage（ChatInput 挂载时恢复）。
     *
     * `skill` 为技能列表里的真实记录——待选标签的显示名取它的 `name`（而非目录名），
     * 与 SkillListPanel.handleCreateSkill 的写法保持一致。
     */
    /**
     * 把提示词模板与技能标签写入 sessionStorage（ChatInput 挂载时恢复）。
     *
     * `skill` 为技能列表里的真实记录。`insert_token: false` 告诉 ChatInput
     * **不要**往输入框插 `/expert-creator` 标签——草稿开头的
     * 「用「expert-creator」技能帮我创建/修改专家：」已经点名了技能，ChatInput
     * 只把该技能记为预选，发送时随 skills 字段交给后端激活。
     */
    writeExpertDraft(text, skill) {
      try {
        sessionStorage.setItem(DRAFT_KEY, text)
        sessionStorage.setItem(
          PENDING_SKILL_KEY,
          JSON.stringify({
            dir_name: CREATE_EXPERT_SKILL_DIR,
            name: (skill && skill.name) || CREATE_EXPERT_SKILL_DIR,
            icon: (skill && skill.icon) || '',
            category_id: (skill && skill.category_id) || 'default',
            insert_token: false,
          })
        )
      } catch (e) {
        // 隐私模式等场景忽略
      }
    },

    /** 在「我的技能」里查 expert-creator；返回技能记录，未安装/接口异常返回 null */
    async findExpertCreatorSkill() {
      try {
        const data = await getUserSkills()
        const list = (data && data.skills) || []
        return list.find((s) => s.dir_name === CREATE_EXPERT_SKILL_DIR) || null
      } catch (e) {
        // 按「未安装」处理，安装失败也不阻断创建流程
        return null
      }
    },

    /**
     * 确保 expert-creator 技能已添加到「我的技能」，并返回该技能记录。
     *
     * 与「创建技能」一致：未安装时自动安装（不然 Agent 拿不到这套创建规范），
     * 已安装则直接复用；409（已添加）忽略。返回的技能记录用于拼待选标签。
     *
     * 注：已添加但被**停用**的技能不在这里重新启用——停用态 Agent 确实不加载，
     * 但重复安装会 409，故交由用户在技能 Tab 里恢复。
     */
    async ensureExpertCreatorSkill() {
      let skill = await this.findExpertCreatorSkill()
      if (skill) return skill
      try {
        await addSkillToUser(CREATE_EXPERT_SKILL_DIR)
      } catch (e) {
        if (!String((e && e.message) || '').includes('已添加')) {
          this.showToast((e && e.message) || '准备创建专家失败', 'error')
        }
      }
      // 安装成功后再取一次，拿到真实显示名；取不到就让 name 回退成目录名
      skill = await this.findExpertCreatorSkill()
      return skill
    },

    /** 「新建专家」：预填创建模板 → 交回 App 跳到新对话 */
    async handleCreateExpert() {
      if (this.preparing) return
      this.preparing = true
      try {
        const skill = await this.ensureExpertCreatorSkill()
        this.writeExpertDraft(CREATE_LEAD + '\n' + CREATE_TEMPLATE.replace('{name}', 'XX'), skill)
        this.$emit('create-expert')
      } finally {
        this.preparing = false
      }
    },

    /**
     * 「编辑」：预填修改模板（不含专家 ID），并把 ID 写入 sessionStorage 供请求
     * 结构化字段携带——专家 ID 不进消息正文（可被用户任意修改），由系统绑定到会话。
     */
    async handleEditExpert(item) {
      if (this.preparing || !item) return
      this.preparing = true
      try {
        const skill = await this.ensureExpertCreatorSkill()
        this.writeExpertDraft(this.buildEditTemplate(item), skill)
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
        } catch (e) {
          // 隐私模式等场景忽略
        }
        this.$emit('edit-expert')
      } finally {
        this.preparing = false
      }
    },

    /** 修改模板：把"要改什么"留给用户补，其余参数一次性给足 Agent。
     *  注意：**不要把专家 ID 写进模板**——ID 由系统经 pending_expert_id 字段绑定。 */
    buildEditTemplate(item) {
      const name = String(item.export_name_cn || '').trim()
      const tags = this.skillTagsOf(item).join('、')
      const desc = String(item.export_description || '').trim() || '（暂无）'
      return (
        EDIT_LEAD + '\n' +
        `【专家名称】${name}\n` +
        `【现有功能描述】${desc}\n` +
        `【现有技能标签】${tags || '（暂无）'}\n` +
        `【原始需求】${clip(item.export_prompt, 800) || '（未记录）'}\n` +
        '\n【我要修改的地方】请在这里补充要调整的内容（例如：更侧重城投债、输出必须标注数据来源）'
      )
    },

    async confirmDelete(item) {
      const ok = window.confirm(
        `确定删除专家「${item.export_name_cn}」吗？\n（默认软删除，历史版本目录会保留）`
      )
      if (!ok) return
      try {
        await deleteExpert(item.export_agent_id, false)
        await this.refreshSilently('mine')
        this.showToast(`已删除「${item.export_name_cn}」`)
      } catch (e) {
        this.showToast(e.message || '删除失败', 'error')
      }
    },
  },
}
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
     此处不写死，避免与全局色板同权重竞争后按注入顺序覆盖配色。 */
  font-size: 16px;
  font-weight: 600;
}

/* 头像配色已收敛到全局 .ea-0 ~ .ea-7（style.css + utils/expertAvatar.js）：
   按 export_agent_id 稳定取档，与 + 号下拉 / 胶囊 / 回答面板标识同色。
   原先按网格序号取色的 av-p0..av-p5 已移除，避免两套色板并存。 */

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
  /* 需求：最小宽度 700px；窄屏时以 94vw 封顶避免溢出 */
  width: 700px;
  min-width: min(700px, 94vw);
  max-width: 94vw;
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

.detail-desc-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-desc {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

.detail-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
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
</style>
