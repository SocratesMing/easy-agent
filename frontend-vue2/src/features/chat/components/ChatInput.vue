<template>
  <div class="chat-input-container">
    <div class="input-box" ref="inputBoxRef">
      <!-- 上传限额汇总提示（整批拒绝 / 文件被跳过时出现，2.6s 自动消失） -->
      <div v-if="uploadNotice" class="upload-notice" role="status" aria-live="polite">
        <span class="upload-notice-text">{{ uploadNotice }}</span>
        <button
          type="button"
          class="upload-notice-close"
          aria-label="关闭提示"
          @click="dismissUploadNotice"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <UploadTray
        v-if="uploadedFiles.length > 0"
        :files="uploadedFiles"
        :limits-text="limitsText"
        :limits-full="limitsFull"
        @remove="removeFile"
        @retry="retryUpload"
      />
      
      <div class="input-row">
        <div class="input-field">
          <div
            v-show="caretLineTop !== null"
            class="caret-line"
            :style="{ top: caretLineTop + 'px', height: lineHeight + 'px' }"
          ></div>
          <!-- 镜像渲染层：真实显示的文字 + 内联技能标签，与 textarea 同字体/行高/宽度，两侧换行逐行一致。
               textarea 文字透明，只负责输入、光标与选区；用户看到的文字都来自这一层（见 syncMirror）。
               技能标签是文本里的一段子串（`／显示名`，首字符是全角斜线）：这里把它渲染成「图标 + 名字」——
               斜线的字形藏掉、图标绝对定位进它 1em 宽的占位框，所以布局宽度与 textarea 分毫不差，
               长文本换行与光标位置逐像素对齐（见 .input-skill-pill-mark 的 CSS 注释）。
               「光标紧邻标签之后」= selectionStart === 标签结束下标，Backspace 据此整段删掉标签；
               点击标签同样整段移除（标签里不放 ×，避免改变宽度）。
               注：Vue 2 保留模板空白，元素之间必须写成紧邻，否则镜像层会多出空格、换行位置就对不上了。 -->
          <div v-show="mirrorText" ref="mirrorRef" class="input-mirror" aria-hidden="true"
            ><template v-for="(seg, index) in mirrorSegments"
              ><span v-if="seg.type === 'text'" :key="index" class="input-mirror-text">{{ seg.text }}</span
              ><span
                v-else
                :key="index"
                class="input-skill-pill"
                :title="`${seg.name}（点击移除）`"
                @click.stop.prevent="removeSkillToken(seg.dirName)"
              ><span class="input-skill-pill-mark">／<SkillIcon :icon="seg.icon" :category="iconCategoryFor(seg.categoryId)" size="xs" /></span>{{ seg.label }}</span
            ></template
          ></div>
          <textarea
            ref="textareaRef"
            v-model="message"
            spellcheck="false"
            v-tip:hover="'输入消息，Enter 发送 · Shift+Enter 换行 · 输入 / 唤起技能选择'"
            @keydown="onKeydown"
            @beforeinput="onBeforeInput"
            @input="onInput"
            @focus="updateCaretLine"
            @blur="hideCaretLine"
            @keyup="updateCaretLine"
            @click="updateCaretLine"
            @scroll="updateCaretLine"
            placeholder=""
            :disabled="disabled"
            rows="1"
          ></textarea>
        </div>
      </div>
      
      <div class="input-actions">
        <div class="left-actions">
            <!-- 「+」添加菜单：添加文件 / 专家 / 技能 / 连接器（Vue 2.7 无 Teleport，菜单留在 wrapper 内） -->
          <div class="plus-menu-wrapper" ref="plusMenuRef">
            <button
              type="button"
              class="action-btn plus-btn"
              :class="{ disabled: isStreaming || disabled }"
              :disabled="isStreaming || disabled"
              v-tip="'添加文件 · 知识库 · 专家 · 技能 · 连接器'"
              aria-haspopup="menu"
              :aria-expanded="showPlusMenu ? 'true' : 'false'"
              @click="togglePlusMenu"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
            <PlusMenu
              :open="showPlusMenu"
              :knowledge-enabled="knowledgeEnabled"
              :menu-style="plusMenuStyle"
              @add-file="handleMenuAddFile"
              @knowledge="openKnowledgeScope"
              @experts="openSubpanel('experts', $event)"
              @skills="openSubpanel('skills', $event)"
              @connectors="openSubpanel('connectors', $event)"
              @hover="hoverPlusItem"
              @hover-in="cancelPlusClose"
              @hover-out="schedulePlusClose"
            />
          </div>

            <div v-if="subpanelCategory" ref="plusSubpanel" class="plus-subpanel" :style="plusSubpanelStyle" @click.stop @mouseenter="onSubpanelEnter" @mouseleave="onSubpanelLeave" @focusin="onSubpanelFocusIn" @focusout="onSubpanelFocusOut">
              <PlusSubpanelHeader
                :title="plusListTitle"
                :count="plusListCount"
                :keyword="subpanelKeyword"
                :placeholder="plusListSearchPlaceholder"
                :searchable="!plusListLoading && !plusListError && hasPanelItems"
                @update:keyword="subpanelKeyword = $event"
              />

              <!-- 专家：上下两区（我的专家 / 公共专家），点击行 = 召唤（再点 = 取消召唤） -->
              <ExpertSubpanelList
                v-if="!plusListLoading && !plusListError && subpanelCategory === 'experts'"
                :loading="plusListLoading"
                :error="plusListError"
                :my-experts="filteredMyExperts"
                :visible-my-experts="visibleMyExperts"
                :public-experts="filteredPublicExperts"
                :visible-public-experts="visiblePublicExperts"
                :keyword="subpanelKeyword"
                :active-expert-id="activeExpert && activeExpert.export_agent_id"
                :stacked="compact"
                @toggle="toggleExpert"
                @more="loadMoreExperts"
                @create="handleMenuPanel('experts')"
                @scroll="onExpertsScroll"
              />

              <!-- 技能 / 连接器：沿用通用条目列表 -->
              <PlusList
                v-else
                :loading="plusListLoading"
                :error="plusListError"
                :empty-text="plusListEmptyText"
                :no-match="filteredPlusList.length === 0"
                :no-match-text="plusListNoMatchText"
                :rows="panelRows"
                :is-connector="isConnectorPanel"
                :selectable="isSkillListRow"
                :selected-keys="selectedSkillDirs"
                :toggling-key="connectorToggling"
                @select="onPlusListItemClick"
                @toggle-connector="toggleConnectorSwitch"
              />
              <!-- 操作反馈：刚改过的连接器开关提示一句，并说明已同步到「专家·技能·连接器」 -->
              <div
                v-if="isConnectorPanel && connectorNotice"
                class="plus-subpanel-notice"
                :class="connectorNoticeType"
                role="status"
                aria-live="polite"
              >
                {{ connectorNotice }}
              </div>
              <div v-if="showManageEntry" class="plus-subpanel-footer">
                <button type="button" class="plus-subpanel-manage" @click="handleMenuPanel(subpanelCategory)">
                  在管理面板中打开 →
                </button>
              </div>
            </div>
          <!-- 知识库引用：仅由加号菜单「知识库」入口打开（常驻触发按钮隐藏），
               面板与技能子面板同款样式，锚到「知识库」菜单项右侧弹出 -->
          <KnowledgeScopeSelector
            ref="knowledgeScope"
            :session-id="sessionId || ''"
            :disabled="isStreaming || disabled"
            hide-trigger
            anchor-selector=".plus-menu-item-knowledge"
            :show-manage="showManageEntry"
            @mouseenter.native="cancelPlusClose"
            @mouseleave.native="schedulePlusClose"
            @create-session="$emit('create-session')"
            @manage="openKnowledgeManage"
          />
          <input
            ref="fileInputRef"
            type="file"
            @change="handleFileSelect"
            multiple
            :disabled="isStreaming || disabled"
            hidden
          />


          <ModelPicker
            :models="models"
            :selected-model="selectedModel"
            :is-streaming="isStreaming"
            :disabled="disabled"
            @update:selectedModel="$emit('update:selectedModel', $event)"
          />

          <button
            class="action-btn web-search-btn"
            :class="{ active: webSearchEnabled, disabled: webSearchUnavailable || isStreaming || disabled }"
            :disabled="isStreaming || disabled"
            v-tip="webSearchTitle"
            @click="toggleWebSearch"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="2" y1="12" x2="22" y2="12"></line>
              <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
            </svg>
          </button>

          <!-- 当前专家标识：输入框工具栏内的胶囊（首字头像 + 名称 + 退出×）。
               与专家 Tab 的「召唤」联动，点击 × 退出专家模式回到通用 Agent。
               兼容性：不使用 flex gap / color-mix（IE 与旧版 Edge 不支持）。 -->
          <div
            v-if="activeExpert && !compact"
            class="expert-chip"
            :title="`当前专家：${activeExpert.export_name_cn}（点击 × 退出专家模式）`"
          >
            <!-- 头像颜色与专家 Tab / 下拉 / 回答面板标识同源（按专家 ID 稳定取色） -->
            <span class="expert-chip-avatar" :class="expertAvatarClass(activeExpert)">{{ expertAvatarChar(activeExpert) }}</span>
            <span class="expert-chip-name">{{ activeExpert.export_name_cn }}</span>
            <button
              type="button"
              class="expert-chip-clear"
              :disabled="isStreaming || disabled"
              aria-label="退出专家模式"
              @click="$emit('clear-expert')"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
        
        <div class="right-actions">
          <div 
            v-if="showTokenRing" 
            ref="ringRef"
            class="context-ring-wrapper" 
            @click.stop="toggleTokenPopup"
          >
            <span class="context-ring-inner">
              <svg class="context-ring" viewBox="0 0 36 36">
                <circle class="context-ring-bg" cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" stroke-width="3" />
                <circle class="context-ring-fill" cx="18" cy="18" r="15.9" fill="none"
                  :stroke="contextColor" stroke-width="3" stroke-linecap="round"
                  :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
                  transform="rotate(-90 18 18)" />
              </svg>
            </span>
            <span class="context-ring-text">{{ contextPercent }}%</span>
            <!-- Vue 2.7 不支持 Teleport：弹窗留在 wrapper 内，依赖 wrapper 无 transform（hover 缩放作用于 .context-ring-inner）保证 fixed 定位正确 -->
              <div v-if="showTokenPopup" class="token-popup" :style="popupStyle" @click.stop>
                <div class="token-popup-title">会话信息</div>
                <div class="token-popup-section">
                  <div class="token-popup-row">
                    <span class="token-popup-label">会话耗时</span>
                    <span class="token-popup-value duration-value">{{ formattedDuration }}</span>
                  </div>
                  <div class="token-popup-row">
                    <span class="token-popup-label">迭代次数</span>
                    <span class="token-popup-value">{{ iterationCount }}</span>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-section">
                <div class="token-popup-row">
                  <span class="token-popup-label">本轮上下文占用</span>
                </div>
                <div class="token-popup-context-row">
                  <span class="token-popup-context-value">{{ formatTokens(sessionUsage.context_tokens) }}/{{ formatTokens(sessionUsage.context_length) }}</span>
                    <span class="token-popup-context-percent" :style="{ color: contextColor }">{{ contextPercent }}%</span>
                  </div>
                  <div class="token-popup-bar">
                    <div class="token-popup-bar-inner">
                      <div class="token-popup-bar-fill" :style="{ width: contextPercent + '%', background: contextColor }"></div>
                    </div>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-row">
                  <span class="token-popup-label">输入 Token</span>
                  <span class="token-popup-value input">{{ formatTokens(sessionUsage.input_tokens) }}</span>
                </div>
                <div class="token-popup-row">
                  <span class="token-popup-label">输出 Token</span>
                  <span class="token-popup-value output">{{ formatTokens(sessionUsage.output_tokens) }}</span>
                </div>
                <div class="token-popup-row">
                  <span class="token-popup-label">思考 Token</span>
                  <span class="token-popup-value reasoning">{{ formatTokens(sessionUsage.reasoning_tokens) }}</span>
                </div>
              </div>
          </div>
          <button 
            v-if="!isStreaming"
            @click="send" 
            class="action-btn send-btn"
            :class="{ active: canSend }"
            :disabled="!canSend || disabled"
            v-tip="'发送（Enter）'"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="5" y1="12" x2="19" y2="12"></line>
              <polyline points="12 5 19 12 12 19"></polyline>
            </svg>
          </button>
          <button
            v-if="isStreaming"
            @click="stop"
            class="action-btn stop-btn"
            v-tip="'停止生成'"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2"></rect>
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 技能选择面板：输入 / 后出现在输入框上方。
         Vue 2.7 不支持 Teleport，用 fixed 定位脱离 .input-box 的 overflow: hidden 裁切 -->
    <div
      v-if="skillPickerOpen"
      ref="skillPickerRef"
      class="skill-picker"
      :style="skillPickerStyle"
      @click.stop
    >
      <div class="skill-picker-header">
        <span>选择技能</span>
        <span class="skill-picker-hint">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
      </div>

      <div v-if="skillPickerLoading" class="skill-picker-state">正在加载技能…</div>
      <div v-else-if="filteredSkills.length === 0" class="skill-picker-state">
        {{ skillOptions.length === 0 ? '还没有可用的技能，先到技能中心添加并启用' : '没有匹配的技能' }}
      </div>

      <ul v-else class="skill-picker-list" role="listbox" aria-label="可用技能">
        <li
          v-for="(skill, index) in filteredSkills"
          :key="skill.dir_name"
          class="skill-picker-item"
          :class="{ active: index === skillPickerIndex }"
          role="option"
          :aria-selected="index === skillPickerIndex ? 'true' : 'false'"
          @mouseenter="skillPickerIndex = index"
          @mousedown.prevent="selectSkill(skill)"
          @click="selectSkill(skill)"
        >
          <SkillIcon :icon="skill.icon" :category="iconCategoryFor(skill.category_id)" size="xs" />
          <span class="skill-picker-body">
            <span class="skill-picker-name">{{ skill.name || skill.dir_name }}</span>
            <span class="skill-picker-desc">{{ skill.description || '暂无描述' }}</span>
          </span>
          <span class="skill-picker-slash">/{{ skill.dir_name }}</span>
        </li>
      </ul>
    </div>

    <div v-if="showFooter" class="input-footer">
      <span class="footer-text">内容由AI生成，请仔细甄别</span>
    </div>
  </div>
</template>

<script>
import { uploadFile, deleteFile } from '../../../api/files.js'
import { getKnowledgeStatus, getWebSearchStatus, getUploadLimits } from '../../../api/settings.js'
import { getUserSkills } from '../../../api/skills.js'
import { iconCategoryFor } from '../../../utils/skillCategories.js'
import { buildMessageText, parseSlashQuery } from '../../../utils/skillSlash.js'
// 内联技能标签：标签文字 = textarea 文本里的子串，选中/删除/发送前的剥离都走这套纯逻辑
import {
  buildSegments,
  buildTokenMap,
  insertSkillToken,
  migrateLegacySkillTokens,
  reconcileTokenDeletion,
  removeTokenSegments,
  skillDirsInText,
  skillSegments,
  stripSkillTokens,
  tokenAfterCaret,
  tokenBeforeCaret,
  tokenForSkill,
} from '../../../utils/skillPillTokens.js'
import { getConnectors, toggleConnector } from '../../../api/connectors.js'
import { emitConnectorsChanged, onConnectorsChanged } from '../../../utils/connectorSync.js'
import { eventTarget } from '../../../utils/domEvent.js'
import { listExperts, listPublicExperts } from '../../../api/expert.js'
// 别名导入：模板要用同名方法（Vue2 模板只能访问实例成员），避免与方法名混淆
import {
  expertAvatarChar as buildExpertAvatarChar,
  expertAvatarClass as buildExpertAvatarClass
} from '../../../utils/expertAvatar.js'
import ExpertSubpanelList from './ExpertSubpanelList.vue'
import PlusSubpanelHeader from './PlusSubpanelHeader.vue'
import PlusList from './PlusList.vue'
import PlusMenu from './PlusMenu.vue'
import ModelPicker from './ModelPicker.vue'
import UploadTray from './UploadTray.vue'
import FileIcon from '../../../shared/components/FileIcon.vue'
import SkillIcon from '../../../shared/components/SkillIcon.vue'
import KnowledgeScopeSelector from '../../knowledge/KnowledgeScopeSelector.vue'

// 镜像层为画出技能芯片的外扩留白，把裁剪盒向外扩了这么多 px（见 .input-mirror 的 padding/margin）：
// 补滚动条占位时要一起加上，否则镜像内容比 textarea 窄、换行位置对不上。
const MIRROR_BLEED = 6

// 「+」子面板的尺寸与贴边间距：JS 定位与 CSS 宽度必须同源，否则面板放大后会溢出视口。
// 单行布局（名称 + 描述同排）需要更宽的面板，380px 下描述只剩 130px 左右不够看。
const SUBPANEL_WIDTH = 480
// 子面板与主菜单之间的水平间隙；CSS 里的 ::before 热桥按「间隙 + 2px 重叠」覆盖这段空白
const SUBPANEL_GAP = 6
// 子面板高度上限。面板整体底边贴主菜单底边、向上生长，可视空间有多大就长多高，
// 但封顶在这个值——否则技能多的用户会顶到屏幕顶端，观感像「整个页面被面板占住」。
// 按行高约 44px 算，560px ≈ 列表能露出 10 行，比原来的 300px 一屏 5 行舒服很多。
const SUBPANEL_MAX_HEIGHT = 560

// 输入草稿持久化：页面刷新后恢复用户尚未发送的输入内容（“输入框的状态不变”）
const DRAFT_KEY = 'easy_agent_input_draft'

// 待选技能：由技能中心「创建技能」等入口写入，本组件挂载时恢复成技能标签
const PENDING_SKILL_KEY = 'easy_agent_pending_skill'

// 待修改专家：由专家面板「编辑」写入（{expert_agent_id, export_name_cn}）。
// 专家 ID 不进消息正文（防篡改），挂载时消费为组件状态，发送时随请求的
// pending_expert_id 字段上行；发送后清空。与后端 save_expert 的 edit_targets 绑定配套。
const PENDING_EXPERT_EDIT_KEY = 'easy_agent_pending_expert_edit'

// 输入框最大高度：5 行（行高 1.6 × 15px = 24px）。文本再长就在框内滚动（右侧滚动条），
// 输入框不再无限增高——与 CSS 的 max-height 保持一致，两处改要一起改。
const TEXTAREA_MAX_ROWS = 5
const TEXTAREA_LINE_HEIGHT = 24
const TEXTAREA_MAX_HEIGHT = TEXTAREA_MAX_ROWS * TEXTAREA_LINE_HEIGHT

export default {
  components: { FileIcon, SkillIcon, KnowledgeScopeSelector, UploadTray, ModelPicker, PlusMenu, ExpertSubpanelList, PlusList, PlusSubpanelHeader },
  props: {
    disabled: { type: Boolean, default: false },
    sessionId: { type: String, default: null },
    isStreaming: { type: Boolean, default: false },
    sessionUsage: { type: Object, default: () => ({ input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: null, auto_compress_tokens: null, context_tokens: 0 }) },
    sessionDuration: { type: Number, default: 0 },
    iterationCount: { type: Number, default: 0 },
    models: { type: Array, default: () => [] },
    selectedModel: { type: String, default: null },
    showFooter: { type: Boolean, default: false },
    draftKey: { type: String, default: DRAFT_KEY },
    // 是否消费技能中心「创建技能」预置的待选技能（只应由主页面输入框消费）
    consumePendingSkill: { type: Boolean, default: true },
    // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
    // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/chat/stream（带 expert_agent_id）。
    activeExpert: { type: Object, default: null },
    // 外部预填的问题（如专家详情弹窗点「专家帮你做」示例）：非空时写入输入框，
    // 写入后立即 $emit('expert-prompt-applied') 通知父组件清空，避免重复触发。
    expertPrompt: { type: String, default: '' },
    // 「+」二级面板底部的「在管理面板中打开」入口（无可跳转的管理面板时隐藏）
    showManageEntry: { type: Boolean, default: true },
    // 窄输入框（AI 侧边栏）：不放专家胶囊（会挤掉发送按钮，已选只在「+」→ 专家面板体现），专家行名称、描述分两行
    compact: { type: Boolean, default: false }
  },
  data() {
    return {
      message: '',
      // 镜像渲染层显示的文字（textarea 文字透明，用户看到的都是这一层，见 syncMirror）
      mirrorText: '',
      uploadedFiles: [],
      // 联网搜索：available=false 时按钮置灰（后端未启用或未配置 api_key）
      webSearchEnabled: false,
      webSearchAvailable: false,
      webSearchReason: '',
      // 上传限额（后端 config.yaml 的 upload_limits 段，经 /agent/settings/upload-limits 下发）：
      // 约束「单个会话、单次对话」的附件，发送后清空即重置。拉取失败保留此默认值，
      // 绝不因接口异常而放行。
      uploadLimits: { enabled: true, maxFileSizeMb: 10, maxFiles: 5, maxTotalMb: 20 },
      // 上传限额汇总提示条文案（空串=不显示，2.6s 自动消失）
      uploadNotice: '',
      knowledgeEnabled: false,
      // 光标所在行高亮（overlay 技术：textareal 本身无法按行上色）
      caretLineTop: null, // 高亮条 top，null 表示隐藏
      lineHeight: 24, // 行高（px）
      showPlusMenu: false,
      subpanelCategory: null,
      plusSubpanelStyle: {},
      // 一级菜单（.plus-menu）的视口坐标（顶边 / 底边）。
      // 子面板的底边活动范围就是 [plusMenuTop, plusMenuBottom]：打开时贴底边（左右两面板
      // 底部齐平），内容变少时底边往上收，最多收到与顶边齐平（面板整个落到菜单上方）。
      // 只在 openSubpanel 里写一次，之后 syncSubpanelBox 反复读。
      plusMenuTop: 0,
      plusMenuBottom: 0,
      // 本次打开以来量到过的「最满」高度，作为顶边基准（见 syncSubpanelBox）。
      subpanelRefHeight: 0,
      // 关闭保护：鼠标是否停在面板上、焦点是否在面板内（搜索框）。
      // 面板会随搜索结果变矮，鼠标可能被「抽走」到面板外 —— 聚焦期间一律不自动关闭，
      // 等失焦且鼠标也不在面板上时才关（见 schedulePlusClose）。
      subpanelHovered: false,
      subpanelFocused: false,
      plusHoverTimer: null,
      plusListCategory: '',
      plusList: [],
      plusListLoading: false,
      plusListError: '',
      // ========== 专家快捷面板（「+」→ 专家）==========
      // 上区：本人创建且「可用」的专家；下区：内置公共（预置）专家。
      myExperts: [],
      publicExperts: [],
      // 两个区块默认各展示 6 位（单行布局后一屏能放下 6 行），滚动到底部再加载 3 位
      plusExpertsLimit: 6,
      plusMenuStyle: {},
      showTokenPopup: false,
      // ========== 会话耗时 ==========
      liveDuration: 0,
      durationTimer: null,
      // ========== 技能选择（输入 / 唤起） ==========
      // 已选技能不是独立数组，而是**文本里的内联标签**：token 形如 `/显示名`（见 utils/skillPillTokens.js）。
      // 标签的可见文字与 textarea 文本逐字相同，所以光标位置、Backspace 判定都是纯文本下标运算，
      // 桌面端 / 移动端 / 连续选多个技能的行为完全一致；选中状态由文本推导，不会与文本脱节。
      // 只有启用状态的技能会被 Agent 加载，停用技能即使手打 /name 也不生效，故选择面板只列启用的。
      knownSkills: [],
      // 上一次 textarea 的文本：移动端兜底自愈要拿它做差分（见 onInput）
      lastInputText: '',
      // 本组件自己发起的建会话（上传文件 / 切连接器开关）：sessionId watcher 据此不剥离技能标签
      selfCreatedSession: false,
      // ========== 连接器开关（「+」→ 连接器） ==========
      // 开关直接对应连接器自身的启用状态（用户 mcp.json 里每个 server 的 enabled），
      // 与「专家·技能·连接器」面板是同一份状态：这里改，那边实时跟着变（见 connectorSync.js）。
      // 正在提交的连接器 id（该行开关暂时禁用，避免连点产生竞态）。
      connectorToggling: '',
      // 操作反馈：改完开关在面板内提示一句，3s 后自动消失
      connectorNotice: '',
      connectorNoticeType: 'success',
      connectorNoticeTimer: null,
      // 子面板搜索（专家 / 技能 / 连接器共用一份，切换分类时清空）
      subpanelKeyword: '',
      skillPickerOpen: false,
      skillPickerIndex: 0,
      skillPickerLoading: false,
      skillOptions: [],
      skillQuery: '',
      slashStart: -1,
      skillPickerStyle: {},
      skillLoadedAt: 0,
      // 预选技能目录名（不以内联标签呈现）：外部入口（如专家面板「新建/修改专家」）
      // 在草稿里用自然语言点名技能（"用「expert-creator」技能帮我创建：…"），
      // 这里只把技能记为已选，发送时随 skills 字段交给后端激活。
      preselectedSkillDirs: [],
      // 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：发送时随请求的
      // pending_expert_id 字段上行，后端校验归属后绑定为 save_expert 的修改目标。
      pendingExpertEditId: '',
    }
  },
  computed: {
    webSearchUnavailable() {
      return !this.webSearchAvailable
    },
    webSearchTitle() {
      if (!this.webSearchAvailable) {
        if (this.webSearchReason === 'disabled') return '联网搜索未启用（后端配置 web_search.enabled 为 false）'
        if (this.webSearchReason === 'unreachable') return '联网搜索状态获取失败，点击重试'
        return '联网搜索未配置（需配置 WEB_SEARCH_API_KEY 后重启后端）'
      }
      return this.webSearchEnabled ? '联网搜索已开启，点击关闭' : '联网搜索（点击开启）'
    },
    /** 计额口径：上传失败的文件不占额度（上传中/已上传都占） */
    chargedFiles() {
      return this.uploadedFiles.filter(f => f.uploadStatus !== 'error')
    },
    usedCount() {
      return this.chargedFiles.length
    },
    usedBytes() {
      return this.chargedFiles.reduce((sum, f) => sum + (f.size || 0), 0)
    },
    failedCount() {
      return this.uploadedFiles.filter(f => f.uploadStatus === 'error').length
    },
    remainBytes() {
      const max = this.mbToBytes(this.uploadLimits.maxTotalMb)
      return Math.max(max - this.usedBytes, 0)
    },
    limitsText() {
      const L = this.uploadLimits
      if (!L.enabled) return ''
      const failedTip = this.failedCount > 0 ? `（${this.failedCount} 个失败未计入）` : ''
      return `已用 ${this.formatSize(this.usedBytes)} / ${L.maxTotalMb}MB · ${this.usedCount} / ${L.maxFiles} 个${failedTip}`
    },
    limitsFull() {
      const L = this.uploadLimits
      return L.enabled && this.usedCount >= L.maxFiles
    },
    plusListTitle() {
      const meta = { experts: '专家', skills: '我的技能', connectors: '我的连接器' }
      return meta[this.plusListCategory] || ''
    },
    /** 标题右侧计数：专家 = 过滤后两区之和；技能 / 连接器 = 过滤后条数 */
    plusListCount() {
      if (this.plusListCategory === 'experts') {
        return this.filteredMyExperts.length + this.filteredPublicExperts.length
      }
      return this.filteredPlusList.length
    },
    /** 常驻搜索框的占位文案（按面板类型） */
    plusListSearchPlaceholder() {
      const meta = { experts: '搜索专家', skills: '搜索技能', connectors: '搜索连接器' }
      return meta[this.plusListCategory] || '搜索'
    },
    /** 有搜索词但无命中时的提示（按面板类型） */
    plusListNoMatchText() {
      const meta = {
        experts: '未找到匹配的专家',
        skills: '未找到匹配的技能',
        connectors: '未找到匹配的连接器',
      }
      return meta[this.plusListCategory] || '未找到匹配的内容'
    },
    /** 当前面板是否有数据：空列表不显示搜索框，免得对着空列表以为「搜不到」 */
    hasPanelItems() {
      if (this.plusListCategory === 'experts') {
        return this.myExpertsActive.length + this.publicExperts.length > 0
      }
      return this.plusList.length > 0
    },
    /** 当前子面板是否为连接器列表 */
    isConnectorPanel() {
      return this.plusListCategory === 'connectors'
    },
    /** 当前子面板实际渲染的行（技能 / 连接器都走搜索结果） */
    panelRows() {
      return this.filteredPlusList
    },
    /**
     * 技能 / 连接器的搜索结果。
     * 名称（含技能目录名）优先命中；描述是一大段说明，几乎每个词都能命中，
     * 因此只在名称完全没命中时才回退到描述（与输入框「/」检索同一取舍）。
     */
    filteredPlusList() {
      const kw = this.subpanelKeyword.trim().toLowerCase()
      if (!kw) return this.plusList
      const byName = this.plusList.filter((item) => {
        const name = String(item.name || '').toLowerCase()
        const dir = String(item.dir_name || '').toLowerCase()
        return name.includes(kw) || dir.includes(kw)
      })
      if (byName.length > 0) return byName
      return this.plusList.filter((item) => String(item.desc || '').toLowerCase().includes(kw))
    },
    /** 我的专家：仅保留「可用」（active）状态，与专家 Tab 的召唤约束一致 */
    myExpertsActive() {
      return this.myExperts.filter((item) => item && item.agent_status === 'active')
    },
    filteredMyExperts() {
      return this.filterExperts(this.myExpertsActive)
    },
    filteredPublicExperts() {
      return this.filterExperts(this.publicExperts)
    },
    /** 上区当前可见条目：搜索时全量展示，否则按 plusExpertsLimit 分页 */
    visibleMyExperts() {
      if (this.subpanelKeyword.trim()) return this.filteredMyExperts
      return this.filteredMyExperts.slice(0, this.plusExpertsLimit)
    },
    /** 下区当前可见条目：同上 */
    visiblePublicExperts() {
      if (this.subpanelKeyword.trim()) return this.filteredPublicExperts
      return this.filteredPublicExperts.slice(0, this.plusExpertsLimit)
    },
    plusListEmptyText() {
      const meta = {
        experts: '暂无专家，可在「专家·技能·连接器」面板创建',
        skills: '还没有自己的技能，可在「专家·技能·连接器」面板添加',
        connectors: '还没有已添加的连接器，可在「专家·技能·连接器」面板添加',
      }
      return meta[this.plusListCategory] || '暂无内容'
    },
    /** 当前子面板是否为技能列表（通用列表同时承载连接器，二者点击行为不同） */
    isSkillListRow() {
      return this.subpanelCategory === 'skills'
    },
    // 模型选择：本地双向绑定，变化时同步父组件
    /** 标签文本 → 技能 的映射（`/显示名` 反解回技能目录名） */
    tokenMap() {
      return buildTokenMap(this.knownSkills)
    },
    /**
     * 镜像层的分段结果：普通文字段 + 技能标签段（标签文字与 textarea 文本逐字相同）。
     * 正在输入 `/查询词` 检索时，半截查询词不渲染成标签（否则边打字边闪）。
     */
    mirrorSegments() {
      return buildSegments(
        this.mirrorText,
        this.tokenMap,
        this.skillPickerOpen ? { suppressStart: this.slashStart } : null
      )
    },
    /** 已选技能目录名（按出现顺序去重）：canSend 与发送时的 skills 字段。
     *  = 输入框内联标签 + 预选技能（preselectedSkillDirs，见 restorePendingSkill）。 */
    selectedSkillDirs() {
      const dirs = this.preselectedSkillDirs.slice()
      for (const dir of skillDirsInText(this.message, this.tokenMap)) {
        if (!dirs.includes(dir)) dirs.push(dir)
      }
      return dirs
    },
    canSend() {
      return this.message.trim() || this.uploadedFiles.length > 0 ||
        this.selectedSkillDirs.length > 0
    },
    /**
     * 技能检索：优先按「目录名 / 技能名」匹配；仅当没有任何名称命中时，才回退到描述匹配。
     * 描述是一大段英文说明，几乎每个字母都能命中（如 m → manipulate / from / time），
     * 直接参与匹配会让检索看起来「完全没生效」，因此降级为兜底。
     */
    filteredSkills() {
      const q = this.skillQuery.trim().toLowerCase()
      if (!q) return this.skillOptions
      const byName = this.skillOptions.filter(s =>
        (s.dir_name || '').toLowerCase().includes(q) ||
        (s.name || '').toLowerCase().includes(q)
      )
      if (byName.length > 0) return byName
      return this.skillOptions.filter(s => (s.description || '').toLowerCase().includes(q))
    },
    // 后台未返回耗时数据（sessionDuration<=0）且「会话信息」弹窗打开时，前端按 1s
    // 间隔本地计时，让会话耗时实时更新；后台有数据时直接用后台值。
    shouldCountLive() {
      return this.showTokenPopup && (!this.sessionDuration || this.sessionDuration <= 0)
    },
    displayDuration() {
      return this.shouldCountLive ? this.liveDuration : (this.sessionDuration || 0)
    },
    formattedDuration() {
      const total = Math.floor(this.displayDuration)
      const h = Math.floor(total / 3600)
      const m = Math.floor((total % 3600) / 60)
      const s = total % 60
      if (h > 0) {
        return `${h}小时${m}分${s}秒`
      }
      if (m > 0) {
        return `${m}分${s}秒`
      }
      return `${s}秒`
    },
    // 首页（尚未进入任何会话）不展示会话级用量：登录/注册后会从全局配置把
    // context_length 写进 sessionUsage，但此时没有任何实际用量，
    // 不能凭这个就显示上下文占用环。
    showTokenRing() {
      if (!this.sessionId) return false
      return this.sessionUsage.input_tokens > 0 || this.sessionUsage.output_tokens > 0 || this.sessionUsage.reasoning_tokens > 0 || this.sessionUsage.context_tokens > 0 || this.sessionDuration > 0 || this.iterationCount > 0
    },
    contextPercent() {
      const u = this.sessionUsage
      if (!u.context_length || u.context_length <= 0) return 0
      // 分子使用「当前轮次的上下文窗口占用」(context_tokens)：即本轮喂给模型的输入 token 数，
      // 与 context_length（上下文窗口上限）对比（已不再统计会话累计总量）。
      const ctxTokens = u.context_tokens || 0
      return Math.min(100, Math.round(ctxTokens / u.context_length * 100))
    },
    contextColor() {
      const p = this.contextPercent
      if (p >= 80) return '#ef4444'
      if (p >= 50) return '#f59e0b'
      return '#22c55e'
    },
    popupStyle() {
      const ring = this.$refs.ringRef
      if (!ring) return {}
      const rect = ring.getBoundingClientRect()
      return {
        position: 'fixed',
        bottom: `${window.innerHeight - rect.top + 10}px`,
        right: `${window.innerWidth - rect.right - 8}px`,
      }
    },
  },
  watch: {
    // 搜索词一变，列表条数就变，面板高度必须跟着重算（底边会随条数上下浮动，
    // 所以变多/变少都要重算；是否关闭由 schedulePlusClose 的「聚焦保护」负责）。
    subpanelKeyword() {
      this.$nextTick(this.syncSubpanelBox)
    },
    shouldCountLive(on) {
      if (on) this.startLiveDuration()
      else this.stopLiveDuration()
    },
    // 会话切换：文件保持原语义——oldVal 为 null（新对话加文件时触发的建会话）不清，
    // 否则会清掉刚选的文件。
    // 技能标签按「用户输入阶段 / 会话展示阶段」严格分区：任何 sessionId 变化都算进入展示阶段
    // （切历史会话 / 新建会话 / 从新建态点开历史会话），把标签从草稿文本里剥掉，
    // 否则标签会残留到历史会话的输入框里。唯一例外是本组件自己发起的建会话（上传文件触发），
    // 由 selfCreatedSession 标记跳过，见 ensureSessionId。
    // 注：连接器开关不再随会话走（它反映的是连接器自身的启用状态，与会话无关）；
    // 「待选技能」（技能中心创建技能）走 sessionStorage、在挂载时消费，不经 watcher，不受影响。
    // 会话切换/新建时清空已添加文件：文件属于原会话，不得残留到新会话。
    // 注意 oldVal 为 null（新对话加文件时触发的建会话）不清，否则会清掉刚选的文件。
    sessionId(newVal, oldVal) {
      if (newVal === oldVal) return
      if (oldVal && newVal !== oldVal) {
        this.uploadedFiles = []
        this.dismissUploadNotice()
      }
      this.subpanelKeyword = ''
      // 技能标签是文本的一部分：切会话时把标签从草稿里剥掉（等价于旧的「令牌复位」）。
      // 本组件自己发起的建会话（上传文件）由上传链路标记，不算切换、保留标签，见 ensureSessionId。
      if (!this.selfCreatedSession) {
        this.message = stripSkillTokens(this.message, this.tokenMap)
      }
      this.selfCreatedSession = false
    },
    message(val) {
      try {
        sessionStorage.setItem(this.draftKey, val)
      } catch (e) {
        // 隐私模式等场景下 sessionStorage 可能不可用，忽略即可
      }
      // 程序化赋值（恢复草稿、切会话、发送后清空）不触发 input 事件，镜像层在这里补同步
      this.$nextTick(() => this.syncMirror())
    },
    disabled(val) {
      if (!val) {
        const ta = this.$refs.textareaRef
        if (ta) ta.focus()
      }
    },
    // 外部预填问题变化时（如专家详情弹窗再点一条示例）写入输入框
    expertPrompt(val) {
      this.applyExpertPrompt(val)
    },
  },
  mounted() {
    // 联网搜索可用性：公共接口，失败时按不可用处理（按钮置灰）
    getWebSearchStatus()
      .then(status => {
        this.webSearchAvailable = Boolean(status && status.configured)
        this.webSearchReason = (status && status.reason) || ''
      })
      .catch(() => { this.webSearchAvailable = false; this.webSearchReason = 'unreachable' })
    getKnowledgeStatus()
      .then(status => { this.knowledgeEnabled = Boolean(status && status.enabled) })
      .catch(() => { this.knowledgeEnabled = false })
    // 恢复刷新前的输入草稿
    try {
      const draft = sessionStorage.getItem(this.draftKey)
      if (draft) {
        this.message = draft
        this.$nextTick(() => this.autoResize())
      }
    } catch (e) {
      // 忽略草稿恢复失败
    }
    // 技能列表先拉一次：草稿里可能留着技能标签，需要靠列表把 `／显示名` 反解回技能
    this.loadSkillOptions()
      .then(() => {
        // 旧版草稿里的半角 / 标签迁移成全角形式（要等技能列表就绪才能识别旧 token）
        if (this.message) {
          const migrated = migrateLegacySkillTokens(this.message, this.tokenMap)
          if (migrated !== this.message) this.message = migrated
        }
      })
      .catch(() => { /* 列表拉取失败时标签按普通文本显示，不影响输入 */ })

    // 恢复待选技能（技能中心「创建技能」等入口预置），读取后即清除
    if (this.consumePendingSkill) this.restorePendingSkill()

    // 消费待修改专家（专家面板「编辑」预置）：ID 存组件状态、不进正文，发送时
    // 随请求的 pending_expert_id 字段上行（后端校验归属后绑定为修改目标）
    try {
      const rawEdit = sessionStorage.getItem(PENDING_EXPERT_EDIT_KEY)
      if (rawEdit) sessionStorage.removeItem(PENDING_EXPERT_EDIT_KEY)
      const parsed = rawEdit ? JSON.parse(rawEdit) : null
      if (parsed && parsed.expert_agent_id) this.pendingExpertEditId = parsed.expert_agent_id
    } catch (e) {
      // 隐私模式等场景忽略
    }

    this.syncMirror()
    // 「专家·技能·连接器」面板里改了开关 → 本面板同步（两个面板不在同一组件树分支，走事件广播）
    this.offConnectorsChanged = onConnectorsChanged(this.onConnectorsChanged)

    // 上传限额：公共接口，失败时保留默认值（10MB / 5 个 / 20MB）
    this.loadUploadLimits()

    document.addEventListener('click', this.closeTokenPopup)
    document.addEventListener('click', this.closeSkillPickerOnClick)
    window.addEventListener('resize', this.handleViewportChange)
    document.addEventListener('click', this.closePlusMenu)
    // 点示例问题会先关闭 SkillCenter 面板 → Chat/ChatInput 重新挂载，
    // 此时 expertPrompt 初始值即非空；watch 只响应"变化"，挂载时的初始值不会触发，
    // 因此在这里补一次（放在草稿恢复之后，确保示例问题覆盖草稿）。
    if (this.expertPrompt) this.applyExpertPrompt(this.expertPrompt)
  },
  beforeDestroy() {
    document.removeEventListener('click', this.closeTokenPopup)
    document.removeEventListener('click', this.closeSkillPickerOnClick)
    window.removeEventListener('resize', this.handleViewportChange)
    document.removeEventListener('click', this.closePlusMenu)
    if (this.offConnectorsChanged) this.offConnectorsChanged()
    if (this.connectorNoticeTimer) clearTimeout(this.connectorNoticeTimer)
    this.cancelPlusClose()
    this.stopLiveDuration()
    if (this._uploadNoticeTimer) clearTimeout(this._uploadNoticeTimer)
  },
  methods: {
    async toggleWebSearch() {
      if (this.isStreaming || this.disabled) return
      // 页面加载可能早于后端配置完成（状态在 mounted 固化）：
      // 灰色态点击时先重查一次，配置就绪则本次点击直接点亮（符合点击意图）。
      if (!this.webSearchAvailable) {
        try {
          const status = await getWebSearchStatus()
          this.webSearchAvailable = Boolean(status && status.configured)
          this.webSearchReason = (status && status.reason) || ''
        } catch (_) {
          /* 查询失败保持不可用 */
        }
        if (!this.webSearchAvailable) return
      }
      this.webSearchEnabled = !this.webSearchEnabled
    },
    applyExpertPrompt(val) {
      if (!val) return
      this.message = val
      this.$nextTick(() => {
        this.autoResize()
        const ta = this.$refs.textareaRef
        if (ta) ta.focus()
      })
      this.$emit('expert-prompt-applied')
    },
    /** 供父组件调用：聚焦输入框并把光标移到末尾（如侧边栏打开时） */
    focusInput() {
      this.$nextTick(() => {
        const ta = this.$refs.textareaRef
        if (!ta || ta.disabled) return
        ta.focus()
        const end = ta.value.length
        ta.setSelectionRange(end, end)
      })
    },
    /** 供父组件调用：替换输入内容（如侧边栏的快捷提问、划词提问） */
    setDraft(text) {
      this.message = text || ''
      this.$nextTick(() => this.autoResize())
      this.focusInput()
    },
    updateCaretLine() {
      this.syncMirror() // 聚焦/点击/按键/滚动都会走到这里，顺带把镜像层对齐（含 scrollTop）
      const ta = this.$refs.textareaRef
      if (!ta) return
      const lh = parseFloat(getComputedStyle(ta).lineHeight)
      if (!isNaN(lh) && lh > 0) this.lineHeight = lh
      const pos = ta.selectionStart ?? 0
      const lineIndex = ta.value.slice(0, pos).split('\n').length - 1
      this.caretLineTop = lineIndex * this.lineHeight - ta.scrollTop
    },
    hideCaretLine() {
      this.caretLineTop = null
    },
    togglePlusMenu() {
      if (this.isStreaming || this.disabled) return
      if (this.showPlusMenu) {
        this.showPlusMenu = false
        this.subpanelCategory = null
        if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
        this.cancelPlusClose()
        return
      }
      // 菜单贴着按钮上方弹出（与模型下拉同款定位方式）
      const anchor = this.$refs.plusMenuRef
      if (anchor) {
        const rect = anchor.getBoundingClientRect()
        this.plusMenuStyle = {
          position: 'fixed',
          bottom: `${window.innerHeight - rect.top + 6}px`,
          left: `${rect.left}px`,
          minWidth: '188px',
        }
      }
      this.subpanelCategory = null
      this.showPlusMenu = true
      // 「+」= 展开连接器面板的触发器：菜单渲染完成后直接把连接器子面板展开在
      // 「连接器」菜单项右侧（面板定位依赖该菜单项元素）。
      this.$nextTick(() => this.openConnectorsSubpanelFromMenu())
    },
    /** 「+」菜单打开时默认展开连接器子面板（定位参照菜单里的「连接器」项） */
    openConnectorsSubpanelFromMenu() {
      const anchor = this.$el.querySelector('.plus-menu-item[data-category="connectors"]')
      if (!anchor) return
      this.openSubpanel('connectors', { currentTarget: anchor })
    },
    closePlusMenu(event) {
      const anchor = this.$refs.plusMenuRef
      // 点击知识库面板内部不算「菜单外」（面板不在菜单 DOM 内，但属于同一浮层交互）
      const scopeRoot = this.$refs.knowledgeScope && this.$refs.knowledgeScope.$el
      const target = eventTarget(event)
      const inScope = scopeRoot && scopeRoot.contains(target)
      if (this.showPlusMenu && anchor && !anchor.contains(target) && !inScope) {
        this.showPlusMenu = false
        this.subpanelCategory = null
        if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      }
    },
    handleMenuAddFile() {
      this.showPlusMenu = false
      this.subpanelCategory = null
      if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      const input = this.$refs.fileInputRef
      if (input) input.click()
    },
    // 二级列表：展示当前用户自己的条目（不跳转页面）
    // 技能 = user-skills（含已停用）；连接器 = 用户 mcp.json 中已安装的内置连接器 + 自定义服务；
    // 专家 = 我的专家列表。
    openPlusList(category) {
      this.plusListCategory = category
      this.plusListLoading = true
      this.plusListError = ''
      this.plusList = []
      // 每次打开都重新拉取（接口很轻，且公共专家目录是服务端实时扫描的）
      this.myExperts = []
      this.publicExperts = []
      this.plusExpertsLimit = 6
      // 三个面板共用一份搜索词：切分类时一律清空，避免把上一个面板的关键字带过来
      this.subpanelKeyword = ''
      if (category === 'connectors') this.clearConnectorNotice()
      this.loadPlusList(category)
    },
    /**
     * 打开二级浮动面板（主菜单常驻），并给首帧一个占位尺寸。
     *
     * 真正的尺寸与位置由 syncSubpanelBox 负责。这里只做四件事：
     * 算左右锚点、记住一级菜单的 top/bottom、写一个预估值占位（同帧就会被 sync 覆盖，
     * 基本不会上屏）、复位「悬停 / 聚焦」两个关闭保护标志与高度基准。
     *
     * 垂直方向的规则（2026-10-24 定稿）：
     * 1. **打开时两个面板底部齐平** —— 子面板底边 = 一级菜单底边。
     * 2. 内容（被搜索）变少时**顶边不动、底边随之上收**，最多收到与一级菜单**顶边**齐平，
     *    此时面板整个落在菜单上方，左下角正好咬住菜单右上角。
     * 详见 syncSubpanelBox。
     *
     * 为什么不用 `bottom: 固定值` 把底边钉死：底边钉在菜单底边时，内容变矮会让顶边
     * 往下移，鼠标原地掉出面板 → mouseleave → 面板关闭。现在允许变矮，靠「搜索框聚焦
     * 期间不自动关闭」兜住（见 schedulePlusClose）。
     */
    openSubpanel(category, event) {
      // click 触发同面板已打开则翻转关闭；hover 触发保持
      if (this.subpanelCategory === category) return
      this.subpanelCategory = category
      // 换面板时先清掉可能残留的关闭计时器（例如从搜索框失焦后立刻点了另一个菜单项）
      this.cancelPlusClose()
      this.subpanelFocused = false
      this.subpanelHovered = false
      this.subpanelRefHeight = 0
      const anchor = event.currentTarget
      const rect = anchor.getBoundingClientRect()
      // 对齐基准是一级菜单容器（.plus-menu）：底边决定「齐平」，顶边是上收的极限
      const menuEl = anchor.closest ? anchor.closest('.plus-menu') : null
      const menuRect = menuEl ? menuEl.getBoundingClientRect() : null
      this.plusMenuTop = menuRect ? menuRect.top : rect.top
      this.plusMenuBottom = menuRect ? menuRect.bottom : rect.bottom
      // 宽度与 CSS 同源，并受视口宽度约束（窄窗口下不许溢出）
      const width = Math.min(SUBPANEL_WIDTH, window.innerWidth - 16)
      const fitsRight = rect.right + SUBPANEL_GAP + width <= window.innerWidth - 8
      const left = fitsRight
        ? rect.right + SUBPANEL_GAP
        : Math.max(8, rect.left - SUBPANEL_GAP - width)
      // 占位：真实高度未知（列表异步加载）。先按「底边贴菜单底边、上限 = 菜单竖向空间」摆一下，
      // sync 随后用真实内容高度校正。height 必须给 auto —— 像素值会把面板撑住。
      const provisionalMax = Math.max(
        120,
        Math.min(SUBPANEL_MAX_HEIGHT, this.plusMenuBottom - Math.max(8, this.plusMenuTop))
      )
      this.plusSubpanelStyle = {
        position: 'fixed',
        left: `${left}px`,
        width: `${width}px`,
        top: `${Math.max(8, this.plusMenuBottom - provisionalMax)}px`,
        height: 'auto',
        maxHeight: `${provisionalMax}px`,
      }
      this.openPlusList(category)
      this.$nextTick(this.syncSubpanelBox)
    },
    /**
     * 计算并应用子面板的位置与高度（打开时、首屏数据到位后、搜索词变化、加载更多时）。
     *
     * 规则：
     * 1. **底边范围**：[一级菜单顶边, 一级菜单底边]。打开 / 内容多 → 贴菜单底边
     *    （于是左右两个面板底部齐平）；内容变少 → 底边往上收，收满就贴菜单顶边。
     * 2. **顶边基准固定**：`top = 菜单底边 - 本次打开以来最满的高度`。内容变少时 top
     *    不动，浮动的只有底边，所以搜索过滤时看到的是「下边框往上走」，而不是整块乱跳。
     * 3. **高度 = 内容自然高度**（允许变矮），封顶 = min(SUBPANEL_MAX_HEIGHT, 菜单底边往上的空间)。
     *    不搞「只增不减」，否则内容少了就是一个空白大面板。
     * 4. 变矮会让鼠标「悬空」掉出面板，由 schedulePlusClose 的聚焦/悬停保护兜住。
     */
    syncSubpanelBox() {
      const el = this.$refs.plusSubpanel
      const dom = el && el.$el ? el.$el : el
      if (!dom) return
      const viewportH = window.innerHeight
      // 拿不到基准时退化到视口下半部，保证仍能算出一个合理位置
      const menuTop = this.plusMenuTop || Math.round(viewportH * 0.6)
      const menuBottom = this.plusMenuBottom || menuTop + 40
      // 可用空间 = 视口顶到菜单底边（底边最低就贴到这里）
      const cap = Math.max(120, Math.min(SUBPANEL_MAX_HEIGHT, menuBottom - 8))
      // 量「内容自然高度」之前必须把上一轮写进去的 height / max-height 都放开，
      // 否则量到的是被压扁的那份高度。
      dom.style.height = 'auto'
      dom.style.maxHeight = 'none'
      const natural = dom.offsetHeight
      // 拿不到高度（面板还没布局）就别动样式，否则会被压成 1px
      if (!natural) return
      const height = Math.min(natural, cap)
      // 本次打开以来量到过的最大内容高度 = 顶边基准（只增不减）：
      // 搜索过滤后顶边不许跟着往下滑，只能底边上收。
      if (height > this.subpanelRefHeight) this.subpanelRefHeight = height
      const refHeight = this.subpanelRefHeight || height
      // 基准顶边：最满时底边正好贴菜单底边 → 打开时左右两面板底部齐平
      let top = menuBottom - refHeight
      // 上收极限：底边最多收到与「菜单顶边」齐平（判据用底边，不能用 top）
      if (top + height < menuTop) top = menuTop - height
      // 兜底：不许越过菜单底边、不许钻出视口顶
      top = Math.max(8, Math.min(top, menuBottom - height))
      // ★ 关键：不写死 height 像素，只给 max-height。面板高度交给内容决定，
      // 搜索后条数变少 → 面板立刻变矮、下边框上收（2026-10-24）；
      // 即使 offsetHeight 量偏，也只影响 top 与上限，不会把面板撑住。
      dom.style.maxHeight = `${refHeight}px`
      this.plusSubpanelStyle = {
        ...this.plusSubpanelStyle,
        top: `${top}px`,
        height: 'auto',
        maxHeight: `${refHeight}px`,
      }
    },
    // ========== 悬停触发二级面板 ==========
    // 鼠标悬停主菜单项即切换对应面板，无需点击；点击仍保留（触屏/键盘回退）。
    // 鼠标离开菜单区域超过 350ms 才整体关闭，且有两个「关闭保护」：
    // 鼠标停在面板上、或焦点还在面板内（正在搜索框里打字）时都不关。
    cancelPlusClose() {
      if (this.plusHoverTimer) {
        clearTimeout(this.plusHoverTimer)
        this.plusHoverTimer = null
      }
    },
    schedulePlusClose() {
      this.cancelPlusClose()
      // 保护一：焦点在面板内（搜索框有光标）。面板会随搜索结果变矮，鼠标可能被
      // 布局变化「抽」到面板外触发 mouseleave —— 此刻不能关，否则就是用户反馈的
      // 「搜索一下就消失」。失焦后再判断（见 onSubpanelFocusOut）。
      if (this.subpanelFocused) return
      // 保护二：鼠标还在面板上
      if (this.subpanelHovered) return
      // 350ms：给「从菜单项斜移进子面板」留足时间（原先 250ms 在面板较远时不够用）。
      // 真正的兜底是 .plus-subpanel::before 的热桥，这里只是余量。
      this.plusHoverTimer = setTimeout(() => {
        this.plusHoverTimer = null
        this.subpanelHovered = false
        this.subpanelFocused = false
        this.showPlusMenu = false
        this.subpanelCategory = null
        if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      }, 350)
    },
    /** 进入面板：取消关闭计时（面板会变矮，鼠标可能中途「悬空」，靠这个标志兜住） */
    onSubpanelEnter() {
      this.subpanelHovered = true
      this.cancelPlusClose()
    },
    onSubpanelLeave() {
      this.subpanelHovered = false
      this.schedulePlusClose()
    },
    /** 焦点进入面板（搜索框 / 行按钮）：锁定，不因鼠标移出而关闭 */
    onSubpanelFocusIn() {
      this.subpanelFocused = true
      this.cancelPlusClose()
    },
    /** 焦点离开面板：只有在面板内部换焦点才算没走（focusout 早于新的 focusin） */
    onSubpanelFocusOut(e) {
      if (e && e.relatedTarget && e.currentTarget && e.currentTarget.contains(e.relatedTarget)) return
      this.subpanelFocused = false
      if (!this.subpanelHovered) this.schedulePlusClose()
    },
    hoverPlusItem(category) {
      this.cancelPlusClose()
      // 知识库与技能/专家一致：悬停即展开面板，无需点击
      if (category === 'knowledge') {
        this.subpanelCategory = null
        if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.openPopover(this.$el.querySelector('.plus-menu-item-knowledge'))
        return
      }
      // 切到其他菜单项时收起知识库面板，避免两个浮层叠着
      if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      if (category === 'experts' || category === 'skills' || category === 'connectors') {
        // openSubpanel 需要事件定位；hover 无事件对象时用菜单项元素
        const el = [...this.$el.querySelectorAll('.plus-menu-item')].find(i => i.textContent.includes(
          { experts: '专家', skills: '技能', connectors: '连接器' }[category]
        ))
        this.openSubpanel(category, { currentTarget: el })
        return
      }
      // 其余项（如添加文件）收起子面板，主菜单保持
      this.subpanelCategory = null
    },
    async loadPlusList(category) {
      try {
        if (category === 'experts') {
          // 并行拉取：我的专家（数据库） + 公共专家（内置目录）
          const [mine, preset] = await Promise.all([
            listExperts({ includeDisabled: false }),
            listPublicExperts(),
          ])
          this.myExperts = mine.items || []
          this.publicExperts = preset.items || []
        } else if (category === 'skills') {
          const data = await getUserSkills()
          this.rememberSkills(data.skills || [])
          this.plusList = (data.skills || []).map((s) => ({
            key: `skill:${s.dir_name}`,
            dir_name: s.dir_name,
            name: s.name,
            desc: s.description || '暂无描述',
            icon: s.icon || '',
            category_id: s.category_id,
            initial: (s.name || s.dir_name).slice(0, 1).toUpperCase(),
            enabled: !!s.enabled,
            tag: s.enabled ? '已启用' : '已停用',
            tagType: s.enabled ? 'on' : 'off',
          }))
        } else if (category === 'experts') {
          const data = await listExperts({ pageSize: 20 })
          this.plusList = (data.items || []).map((e) => ({
            key: `expert:${e.export_agent_id}`,
            name: e.export_name_cn,
            desc: String(e.export_description || '').replace(/\s*\n+\s*/g, ' ').trim() || '暂无描述',
            icon: '',
            initial: String(e.export_name_cn || '').trim().slice(0, 1).toUpperCase() || '专',
            tag: e.export_version ? `v${e.export_version}` : '',
            tagType: 'off',
          }))
        } else if (category === 'connectors') {
          const data = await getConnectors()
          // connectorId = 会话开关的标识，也是 mcp.json 的 server key（目录条目用 id，
          // 自定义服务用 server 名）；mcp.json 层面已停用的项仍列出，但标出「已停用」。
          const installed = (data.connectors || [])
            .filter((c) => c.installed)
            .map((c) => ({
              key: `connector:${c.id}`,
              connectorId: c.id,
              name: c.name,
              desc: c.description || '暂无描述',
              icon: '',
              initial: (c.name || c.id).slice(0, 1).toUpperCase(),
              // mcp.json 里的启用状态：会话开关必须与它一致（停用 = 开关关闭），
              // 「专家·技能·连接器」面板显示的也是这个值
              enabled: c.enabled !== false,
              // 启用时展示的 tag 单独存一份：开关来回切时按它还原（停用统一显示「已停用」）
              enabledTag: '已添加',
              enabledTagType: 'on',
              tag: c.enabled === false ? '已停用' : '已添加',
              tagType: c.enabled === false ? 'off' : 'on',
            }))
          const custom = (data.custom || []).map((c) => ({
            key: `custom:${c.name}`,
            connectorId: c.name,
            name: c.name,
            desc: '用户自定义 MCP 服务',
            icon: '',
            initial: (c.name || '').slice(0, 1).toUpperCase(),
            enabled: c.enabled !== false,
            enabledTag: '自定义',
            enabledTagType: 'custom',
            tag: c.enabled === false ? '已停用' : '自定义',
            tagType: c.enabled === false ? 'off' : 'custom',
          }))
          this.plusList = installed.concat(custom)
        }
      } catch (e) {
        this.plusListError = (e && e.message) || '加载失败'
      } finally {
        this.plusListLoading = false
        // 首屏数据到位后高度才稳定：再算一次，把「按预估值占位」的顶边校正成真实内容
        // 对应的位置（否则连接器这类短列表会跟左侧菜单脱开）。
        this.$nextTick(this.syncSubpanelBox)
      }
    },
    // ========== 「+」菜单通用条目行：技能挂令牌 / 连接器挂胶囊 ==========
    /** 该技能是否已在输入框文本里（与「/」唤起技能共用同一套内联标签） */
    isSkillSelected(dirName) {
      if (!dirName) return false
      return this.selectedSkillDirs.includes(dirName)
    },
    /** 通用条目行是否可点选：只有技能行可点（挂成输入框令牌）；连接器行由行尾开关控制 */
    isPlusRowSelectable() {
      return this.isSkillListRow
    },
    /** 停用技能行不可点：Agent 只加载已启用的技能，挂上去也不会生效 */
    isPlusRowDisabled(item) {
      return this.isSkillListRow && !item.enabled
    },
    /** 行尾「已选」态：技能看已挂令牌（连接器行改用开关） */
    isPlusRowSelected(item) {
      return this.isSkillListRow && this.isSkillSelected(item.dir_name)
    },
    /** 行悬浮提示：技能给「选择/已停用」；连接器行由开关自身的 title 承载 */
    plusRowTitle(item) {
      if (this.isSkillListRow) {
        return item.enabled ? `选择「${item.name}」` : '该技能已停用'
      }
      return ''
    },
    /**
     * 通用条目列表行点击：技能行插入 / 移除内联标签 —— 与「/」唤起技能共用同一套标签，
     * 且不收起菜单便于连续选多个。连接器行不再响应整行点击（启用状态由行尾开关控制）。
     */
    onPlusListItemClick(item) {
      if (this.isSkillListRow) {
        this.toggleSkillFromPlus(item)
      }
    },
    /** 收起「+」菜单与其二级面板（含待执行的悬停关闭定时器、知识库浮层） */
    hidePlusMenu() {
      this.showPlusMenu = false
      this.subpanelCategory = null
      if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      this.cancelPlusClose()
    },
    /**
     * 点击技能行 = 插入 / 移除技能标签，行为与「/」唤起技能完全一致（标签落在光标处，光标停在标签之后）。
     * 停用技能不参与：Agent 只加载已启用的技能，挂上去也不会生效。
     */
    toggleSkillFromPlus(item) {
      if (!this.isSkillListRow || !item || !item.dir_name || !item.enabled) return
      if (this.isSkillSelected(item.dir_name)) {
        this.removeSkillToken(item.dir_name)
        return
      }
      this.rememberSkills([{ ...item, description: item.description || item.desc }])
      const ta = this.$refs.textareaRef
      const base = (ta && ta.value) || this.message
      const caret = ta && typeof ta.selectionStart === 'number' ? ta.selectionStart : base.length
      this.replaceTextWithCaret(insertSkillToken(base, caret, tokenForSkill(item)))
      console.log(`[${new Date().toISOString()}] [技能选择] ${item.dir_name}`)
    },
    // ========== 连接器开关（「+」→ 连接器，与「专家·技能·连接器」同一份状态） ==========
    /** 连接器标识（目录条目用 id，自定义服务用 server 名，即 mcp.json 的 server key） */
    connectorKeyOf(item) {
      return (item && item.connectorId) || ''
    },
    /**
     * 连接器在 mcp.json 里是否启用。
     * 「专家·技能·连接器」面板的开关读的是同一个字段：两处必须显示同一状态。
     */
    connectorEnabled(item) {
      return !!item && item.enabled !== false
    },
    /** 该连接器的开关是否为「打开」 */
    isConnectorOn(item) {
      return !!this.connectorKeyOf(item) && this.connectorEnabled(item)
    },
    /**
     * 开关是否不可点。
     * 启用与停用都允许在本面板直接切换（与「专家·技能·连接器」面板同一份状态），
     * 只有「正在提交该行」时才临时禁用，避免连点产生竞态。
     */
    isConnectorSwitchDisabled(item) {
      const key = this.connectorKeyOf(item)
      if (!key) return true
      return this.connectorToggling === key
    },
    /** 该行开关是否正在提交（显示忙碌态的转圈） */
    isConnectorBusy(item) {
      const key = this.connectorKeyOf(item)
      return !!key && this.connectorToggling === key
    },
    /** 开关的无障碍名称 */
    connectorSwitchLabel(item) {
      if (this.isConnectorBusy(item)) return `正在保存连接器 ${item.name} 的开关`
      return `${this.isConnectorOn(item) ? '停用' : '启用'}连接器 ${item.name}`
    },
    /** 开关的 title：说清点下去会发生什么、以及会同步到哪里 */
    connectorSwitchTitle(item) {
      if (this.isConnectorBusy(item)) return '正在保存…'
      return this.isConnectorOn(item)
        ? '已启用（点击停用，「专家·技能·连接器」面板同步生效）'
        : '已停用（点击启用，「专家·技能·连接器」面板同步生效）'
    },
    /** 面板内的操作反馈（连接器开关），ms 后自动消失 */
    showConnectorNotice(message, type = 'success', ms = 2600) {
      if (this.connectorNoticeTimer) clearTimeout(this.connectorNoticeTimer)
      this.connectorNotice = message
      this.connectorNoticeType = type
      this.connectorNoticeTimer = setTimeout(() => {
        this.connectorNoticeTimer = null
        this.connectorNotice = ''
      }, ms)
    },
    /** 清掉提示（打开面板时调用，避免看到上一次操作留下的文案） */
    clearConnectorNotice() {
      if (this.connectorNoticeTimer) clearTimeout(this.connectorNoticeTimer)
      this.connectorNoticeTimer = null
      this.connectorNotice = ''
    },
    /** 按最新状态刷新某个连接器行的开关与 tag（tag 停用时统一显示「已停用」） */
    applyConnectorState(id, enabled) {
      if (!id) return
      const item = this.plusList.find(i => this.connectorKeyOf(i) === id)
      if (!item) return
      item.enabled = !!enabled
      item.tag = item.enabled ? (item.enabledTag || '') : '已停用'
      item.tagType = item.enabled ? (item.enabledTagType || 'on') : 'off'
    },
    /** 其它面板（「专家·技能·连接器」）改了开关：同步本面板显示（忽略自己派发的回声） */
    onConnectorsChanged(payload) {
      if (!payload || payload.source === 'plus-panel') return
      this.applyConnectorState(payload.id, payload.enabled)
    },
    /**
     * 切换连接器的启用状态：直接改连接器自身的开关（后端写用户 mcp.json 的 enabled，
     * 全局生效），并广播给「专家·技能·连接器」面板，保证两处状态一致。
     * 启用 → 停用、停用 → 启用都走这里，两个面板都可以操作。
     */
    async toggleConnectorSwitch(item) {
      const id = this.connectorKeyOf(item)
      if (!id) return
      // 同一行正在提交时忽略连点（按钮此时也已 disabled）
      if (this.connectorToggling) return
      const next = !this.connectorEnabled(item)
      this.connectorToggling = id
      // 本面板先乐观更新（点下去立即有反馈，不等接口往返）
      this.applyConnectorState(id, next)
      try {
        await toggleConnector(id, next)
        // 只在真正改成功后才广播：对方收到后会重新拉取，若在请求途中广播，
        // 它拉到的还是旧数据，会出现「跟着变一下又弹回去」的抖动
        emitConnectorsChanged(id, next, 'plus-panel')
        console.log(
          `[${new Date().toISOString()}] [连接器开关] ${item.name} -> ${next ? '启用' : '停用'}`
        )
        this.showConnectorNotice(
          `已${next ? '启用' : '停用'}「${item.name}」，已同步到「专家·技能·连接器」`
        )
      } catch (e) {
        // 后端没改成功：只回滚本面板（对方从未收到通知，保持原状态即可）
        this.applyConnectorState(id, !next)
        this.showConnectorNotice(e.message || '切换连接器开关失败', 'error', 4000)
      } finally {
        this.connectorToggling = ''
      }
    },
    // ========== 专家快捷面板：行渲染与选择 ==========
    // 头像取字与配色统一走 utils/expertAvatar.js（全局 8 色板 .ea-0 ~ .ea-7），
    // 按 export_agent_id 稳定取档，与技能中心 / 胶囊 / 消息标识同色。
    expertAvatarChar(item) {
      return buildExpertAvatarChar(item)
    },
    expertAvatarClass(item) {
      return buildExpertAvatarClass(item)
    },
    /** 行右侧的浅灰小字描述：优先功能描述，回退专家描述 */
    expertDesc(item) {
      return (
        String((item && item.export_description) || '').replace(/\s*\n+\s*/g, ' ').trim() ||
        String((item && item.export_prompt) || '').replace(/\s*\n+\s*/g, ' ').trim() ||
        '暂无描述'
      )
    },
    /** 是否为当前会话已召唤的专家（activeExpert 由 App.vue 传入） */
    isExpertSelected(item) {
      if (!item || !this.activeExpert) return false
      return this.activeExpert.export_agent_id === item.export_agent_id
    },
    /**
     * 点击专家行：与专家 Tab 的「召唤」同一链路。
     * 已选中的再点一次 = 取消召唤（同时清掉输入框右下角的专家胶囊）。
     * 操作完成后直接关闭面板（选中态由胶囊与「已选」标承载）。
     */
    toggleExpert(item) {
      if (!item || !item.export_agent_id) return
      if (this.isExpertSelected(item)) {
        this.$emit('clear-expert')
      } else {
        this.$emit('select-expert', item)
      }
      if (this.compact) return
      this.showPlusMenu = false
      this.subpanelCategory = null
      this.cancelPlusClose()
    },
    /** 列表滚动到底部附近时，每区多放出 3 条（防止面板一次性过高） */
    onExpertsScroll(e) {
      const el = e && e.target
      if (!el) return
      const reachedBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8
      if (!reachedBottom) return
      this.loadMoreExperts()
    },
    /**
     * 专家搜索过滤：名称 / 描述 / 依赖技能名。
     * 带上技能名是因为用户常按「这个专家用了什么技能」来找，而描述里未必出现该词。
     */
    filterExperts(list) {
      const kw = this.subpanelKeyword.trim().toLowerCase()
      if (!kw) return list
      return (list || []).filter((item) => {
        if (!item) return false
        const name = String(item.export_name_cn || '').toLowerCase()
        const desc = String(item.export_description || '').toLowerCase()
        const skills = Array.isArray(item.export_skills_names)
          ? item.export_skills_names.join(' ').toLowerCase()
          : String(item.export_skills_names || '').toLowerCase()
        return name.includes(kw) || desc.includes(kw) || skills.includes(kw)
      })
    },
    /** 每区多放出 3 条（滚动到底 / 点「还有 N 位」都会走到这里） */
    loadMoreExperts() {
      const total = Math.max(this.myExpertsActive.length, this.publicExperts.length)
      if (this.plusExpertsLimit < total) {
        this.plusExpertsLimit += 3
        // 内容变多是唯一允许面板长高的场景（向上长），重算一次尺寸
        this.$nextTick(this.syncSubpanelBox)
      }
    },
    // 列表底部入口：跳到「专家·技能·连接器」面板做完整管理
    handleMenuPanel(tab) {
      this.showPlusMenu = false
      this.subpanelCategory = null
      this.$emit('open-panel', tab)
    },
    // 加号菜单「知识库」：与技能子面板一致，点击保持主菜单常驻，面板贴菜单项右侧弹出
    openKnowledgeScope(event) {
      this.cancelPlusClose()
      this.subpanelCategory = null
      if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.openPopover(event && event.currentTarget)
    },
    // 知识库子面板底部入口：关闭菜单并打开知识库工作台
    openKnowledgeManage() {
      this.showPlusMenu = false
      this.subpanelCategory = null
      if (this.$refs.knowledgeScope) this.$refs.knowledgeScope.closePopover()
      this.$emit('open-knowledge')
    },
    // 供父组件把预置问题完整文本填进输入框（卡片上最多显示 3 行，长问题展示不全）
    setMessage(text) {
      this.message = text || ''
      this.$nextTick(() => {
        const ta = this.$refs.textareaRef
        if (ta) ta.focus()
      })
    },
    getMessage() {
      return this.message
    },
    startLiveDuration() {
      this.stopLiveDuration()
      this.liveDuration = 0
      this.durationTimer = setInterval(() => {
        this.liveDuration += 1
      }, 1000)
    },
    stopLiveDuration() {
      if (this.durationTimer) {
        clearInterval(this.durationTimer)
        this.durationTimer = null
      }
    },
    toggleTokenPopup() {
      this.showTokenPopup = !this.showTokenPopup
    },
    closeTokenPopup() {
      if (this.showTokenPopup) {
        this.showTokenPopup = false
      }
    },
    formatTokens(n) {
      if (!n) return '0'
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
      return n.toString()
    },
    formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B'
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
      return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    },
    mbToBytes(mb) {
      return (Number(mb) || 0) * 1024 * 1024
    },
    /** 拉取上传限额（公共接口）；失败保留默认值 10MB / 5 个 / 20MB */
    async loadUploadLimits() {
      try {
        const data = await getUploadLimits()
        const toPositive = (v, fallback) => (Number(v) > 0 ? Number(v) : fallback)
        this.uploadLimits = {
          enabled: data && data.enabled !== false,
          maxFileSizeMb: toPositive(data && data.max_file_size_mb, 10),
          maxFiles: toPositive(data && data.max_files_per_message, 5),
          maxTotalMb: toPositive(data && data.max_total_size_mb, 20),
        }
      } catch (e) {
        console.error('获取上传限额失败，使用默认限额（10MB / 5 个 / 20MB）:', e)
      }
    },
    /** 汇总提示条：2.6s 自动消失，可手动关闭 */
    showUploadNotice(text) {
      if (!text) return
      this.uploadNotice = text
      if (this._uploadNoticeTimer) clearTimeout(this._uploadNoticeTimer)
      this._uploadNoticeTimer = setTimeout(() => {
        this.uploadNotice = ''
      }, 2600)
    },
    dismissUploadNotice() {
      if (this._uploadNoticeTimer) clearTimeout(this._uploadNoticeTimer)
      this.uploadNotice = ''
    },
    /**
     * 选出本批可以上传的文件（入列前校验，避免"传了一半才发现超限"）。
     *
     * 规则：
     * - enabled=false 时不限；
     * - 数量不满足（已有非失败数 + 本批数 > 上限）→ **整批拒绝**，让用户重新选择；
     * - 逐个文件检查单文件大小与总大小 → **只跳过超限的那一个**，其余照常上传；
     * - 失败文件不占额度，因此删除/失败都会立即释放名额与空间。
     */
    pickAcceptable(files) {
      const L = this.uploadLimits
      if (!L.enabled) return { accepted: [...files], rejected: [], batchRejected: false }

      let count = this.usedCount
      let total = this.usedBytes
      const accepted = []
      const rejected = []

      if (count + files.length > L.maxFiles) {
        return {
          accepted: [],
          rejected: files.map(f => ({ file: f, reason: 'count' })),
          batchRejected: true,
          message: `最多只能上传 ${L.maxFiles} 个文件（已用 ${count} 个），请重新选择`,
        }
      }

      for (const file of files) {
        if (file.size > this.mbToBytes(L.maxFileSizeMb)) {
          rejected.push({ file, reason: 'file_size' })
          continue
        }
        if (total + file.size > this.mbToBytes(L.maxTotalMb)) {
          rejected.push({ file, reason: 'total' })
          continue
        }
        accepted.push(file)
        count += 1
        total += file.size
      }
      return { accepted, rejected, batchRejected: false }
    },
    /** 把被拒文件拼成一条汇总提示 */
    buildRejectNotice(result, acceptedCount) {
      const L = this.uploadLimits
      const parts = result.rejected.map(({ file, reason }) => {
        if (reason === 'file_size') {
          return `${file.name}（超过单文件上限 ${L.maxFileSizeMb}MB）`
        }
        if (reason === 'total') {
          return `${file.name}（超出剩余空间 ${this.formatSize(this.remainBytes)}，总上限 ${L.maxTotalMb}MB）`
        }
        return file.name
      })
      const head = acceptedCount > 0
        ? `已添加 ${acceptedCount} 个文件，忽略 ${result.rejected.length} 个：`
        : `无法上传，已忽略 ${result.rejected.length} 个文件：`
      return head + parts.join('；')
    },
    /** 重新上传一个失败的文件（复用同一个 File 对象，无需用户重新选择） */
    retryUpload(index) {
      const item = this.uploadedFiles[index]
      if (!item || item.uploadStatus !== 'error' || !item.retryable) return

      // 失败文件不占额度，所以重试等价于「新增一个文件」，同样受限额约束：
      // 已满名额 / 超出剩余空间 / 超过单文件上限 时不允许重新上传
      const L = this.uploadLimits
      if (L.enabled) {
        if (this.usedCount + 1 > L.maxFiles) {
          this.showUploadNotice(`最多只能上传 ${L.maxFiles} 个文件（已用 ${this.usedCount} 个），无法重新上传 ${item.filename}`)
          return
        }
        if (item.size > this.mbToBytes(L.maxFileSizeMb)) {
          this.showUploadNotice(`${item.filename} 超过单文件上限 ${L.maxFileSizeMb}MB，无法重新上传`)
          return
        }
        if (this.usedBytes + item.size > this.mbToBytes(L.maxTotalMb)) {
          this.showUploadNotice(`${item.filename} 超出剩余空间 ${this.formatSize(this.remainBytes)}，无法重新上传`)
          return
        }
      }

      item.uploadStatus = 'uploading'
      item.uploadProgress = 0
      item.errorMessage = ''
      item.retryable = false
      item.cancelled = false
      this.uploadedFiles = [...this.uploadedFiles]
      this.uploadOneFile(item)
    },
    /**
     * 确保会话存在并返回 sessionId（并行上传共享同一个创建 Promise，
     * 避免一次选择多个文件时重复触发 create-session）。
     *
     * 本组件自己发起的建会话会让 sessionId 变化：watcher 据此只跳过「技能标签剥离」，
     * 此刻仍是「用户输入阶段」（文件刚选、技能刚选），输入框里的标签要留着。
     * 连接器开关也走这里：新建态下切换开关时先建会话，再按 session_id 提交给后端。
     */
    ensureSessionId() {
      if (this.sessionId) return Promise.resolve(this.sessionId)
      if (!this._pendingSessionPromise) {
        this._pendingSessionPromise = new Promise((resolve) => {
          // 创建一个临时的监听器来等待会话创建完成
          const unwatch = this.$watch(
            () => this.sessionId,
            (newSessionId) => {
              if (newSessionId) {
                unwatch()
                resolve(newSessionId)
              }
            }
          )
          // 触发会话创建（标记为「自己发起」，watcher 据此不剥离输入框里的技能标签）
          this.selfCreatedSession = true
          this.$emit('create-session')
        }).then((sessionId) => sessionId)
      }
      return this._pendingSessionPromise
    },
    /** 单个文件的上传流程（各文件并行执行，互不阻塞） */
    async uploadOneFile(fileItem) {
      const file = fileItem.file
      try {
        const sessionId = await this.ensureSessionId()
        if (!sessionId) {
          throw new Error('会话创建失败')
        }

        // 等待会话创建期间被用户点 × 删除：不再上传
        if (fileItem.cancelled) return

        // 调用实际的上传接口；signal 用于外部中断（删除上传中的文件）
        const controller = new AbortController()
        fileItem.abortController = controller
        const response = await uploadFile(sessionId, file, (progress) => {
          // 找到对应的文件项并更新进度
          if (fileItem.cancelled) return
          const index = this.uploadedFiles.findIndex(f => f.file === file)
          if (index !== -1) {
            this.uploadedFiles[index].uploadProgress = progress
            // 强制触发更新
            this.uploadedFiles = [...this.uploadedFiles]
          }
        }, controller.signal)

        // 上传期间被用户删除：丢弃结果
        if (fileItem.cancelled) return

        // 找到对应的文件项并更新状态
        const index = this.uploadedFiles.findIndex(f => f.file === file)
        if (index !== -1) {
          this.uploadedFiles[index].uploadStatus = 'completed'
          this.uploadedFiles[index].uploadProgress = 100
          this.uploadedFiles[index].filePath = response.file_path
          this.uploadedFiles[index].id = response.id
          // 强制触发更新
          this.uploadedFiles = [...this.uploadedFiles]
        }

        console.log('文件上传成功:', response)
      } catch (error) {
        // 用户主动取消，不算失败
        if (fileItem.cancelled) return
        // 找到对应的文件项并更新状态
        const index = this.uploadedFiles.findIndex(f => f.file === file)
        if (index !== -1) {
          const reason = (error && error.message) || '上传失败'
          // 超过单文件大小限制的失败不给「重试」（重试也没用），其余可重试
          const isSizeLimit =
            (error && error.code === 'UPLOAD_FILE_TOO_LARGE') ||
            /不能超过\s*\d+(\.\d+)?\s*MB/.test(reason)
          this.uploadedFiles[index].uploadStatus = 'error'
          this.uploadedFiles[index].errorMessage = reason
          this.uploadedFiles[index].retryable = !isSizeLimit
          // 强制触发更新
          this.uploadedFiles = [...this.uploadedFiles]
        }
        console.error('文件上传失败:', error)
      }
    },
    async handleFileSelect(event) {
      const files = Array.from(event.target.files)
      // 先清空 input，避免同一文件二次选择不触发 change
      event.target.value = ''
      if (files.length === 0) return

      // 入列前校验：通过的文件才入列、才上传
      const result = this.pickAcceptable(files)

      if (result.batchRejected) {
        // 数量不满足：整批拒绝，让用户重新选择
        this.showUploadNotice(result.message)
        return
      }
      if (result.rejected.length > 0) {
        // 单文件超限 / 超出剩余空间：只跳过这些文件，其余照常上传
        this.showUploadNotice(this.buildRejectNotice(result, result.accepted.length))
      }

      // 并行上传：每个文件独立开始、独立进度，互不排队
      for (const file of result.accepted) {
        // 创建文件项
        const fileItem = {
          file: file,
          filename: file.name,
          size: file.size,
          uploadStatus: 'uploading',
          uploadProgress: 0,
          cancelled: false,
          abortController: null,
          errorMessage: '',
          retryable: false
        }

        // 添加到数组
        this.uploadedFiles.push(fileItem)

        // 强制触发初始渲染
        this.uploadedFiles = [...this.uploadedFiles]

        this.uploadOneFile(fileItem)
      }

      event.target.value = ''
    },
    async removeFile(index) {
      const file = this.uploadedFiles[index]
      if (!file) return

      // 标记取消：排队/等待会话中的不再上传，上传中的立即中断请求
      file.cancelled = true
      if (file.uploadStatus === 'uploading' && file.abortController) {
        try {
          file.abortController.abort()
        } catch (e) {
          /* 忽略中断异常 */
        }
      }

      // 如果文件已经上传成功，调用后台的删除接口
      if (file.uploadStatus === 'completed' && file.id && this.sessionId) {
        try {
          await deleteFile(this.sessionId, file)
          console.log('文件删除成功:', file.filename)
        } catch (error) {
          console.error('文件删除失败:', error)
        }
      }

      // 从前端列表中移除文件
      this.uploadedFiles.splice(index, 1)
      // 强制触发更新
      this.uploadedFiles = [...this.uploadedFiles]
    },
    async send() {
      if (!this.canSend || this.disabled) return

      // 等待所有文件上传完成
      const uploadingFiles = this.uploadedFiles.filter(f => f.uploadStatus === 'uploading')
      if (uploadingFiles.length > 0) {
        // 显示上传中提示
        console.log('文件正在上传中，请稍候...')
        // 可以添加一个loading状态或提示信息
        return
      }

      // 只发送上传成功的文件：失败文件（无 id/file_path）不随消息发出、不在气泡中展示
      const failedFiles = this.uploadedFiles.filter(f => f.uploadStatus === 'error')
      if (failedFiles.length > 0) {
        console.warn(`[发送消息] ${failedFiles.length} 个文件上传失败，已从本次发送中剔除:`,
          failedFiles.map(f => f.filename).join(', '))
      }
      const filesToSend = this.uploadedFiles
        .filter(f => f.uploadStatus === 'completed')
        .map(f => ({
          id: f.id,
          filename: f.filename,
          size: f.size,
          file: f.file,
          file_path: f.filePath || null,
          type: f.file?.type || f.fileType || ''
        }))

      // 详细日志：记录发送操作、文件上传
      const ts = new Date().toISOString()
      console.log(`[${ts}] [发送消息] 内容: "${this.message.substring(0, 100)}" | 文件数: ${filesToSend.length}`)
      if (filesToSend.length > 0) {
        console.log(`[${ts}] [文件上传] ${filesToSend.map(f => `${f.filename}(${f.size}B)`).join(', ')}`)
      }

      // 技能以技能目录名提交（后端负责把 `/目录名` 拼进模型输入）；输入框里的内联标签只是 UI，
      // 正文发送前会把标签整段剥掉（见 buildPayload）。连接器不随消息提交：本会话启用哪些连接器
      // 由「+」面板的开关单独设置。
      const turnSkills = this.selectedSkillDirs
      // 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：随请求结构化字段上行，
      // 后端校验归属后绑定为 save_expert 的修改目标
      const expertEditId = this.pendingExpertEditId
      this.$emit(
        'send',
        this.buildPayload(),
        filesToSend,
        null,
        true,
        this.webSearchEnabled,
        turnSkills,
        expertEditId
      )

      this.message = ''
      this.uploadedFiles = []
      this.selectedSkills = []
      // 预选技能与待修改专家已随本条消息提交，清掉避免影响下一条
      this.preselectedSkillDirs = []
      this.pendingExpertEditId = ''
      this.dismissUploadNotice()
      this.closeSkillPicker()
      this.$nextTick(() => this.autoResize())
    },

    /**
     * 发送的消息正文：剥掉内联技能标签、只归一化用户输入，不拼任何指令前缀。
     * 已选技能以 skills（目录名）参数带给后端（见 send()）。
     */
    buildPayload() {
      return buildMessageText(stripSkillTokens(this.message, this.tokenMap))
    },
    stop() {
      this.$emit('stop')
    },
    autoResize() {
      const ta = this.$refs.textareaRef
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, TEXTAREA_MAX_HEIGHT) + 'px'
        this.syncMirror() // 高度变化会改变 textarea 的可滚动区间（scrollTop 被钳制），镜像层要跟上
      }
    },
    /**
     * 把 textarea 的内容/滚动/滚动条占位同步给镜像渲染层。
     * 输入（含输入法合成中）、程序化赋值、滚动后都要调，否则两侧文字会对不上。
     */
    syncMirror() {
      const ta = this.$refs.textareaRef
      const mirror = this.$refs.mirrorRef
      if (!ta || !mirror) return
      // 直接读 textarea：输入法合成中的文字 v-model 还没提交，镜像也要实时显示
      this.mirrorText = ta.value
      this.lastInputText = ta.value
      // 出现纵向滚动条后 textarea 内容区变窄，镜像补同等右内边距，保证两侧换行位置一致
      // （镜像自身还有 MIRROR_BLEED 的 padding 用于扩裁剪盒，要一起算进去，否则内容窄 6px 导致换行错位）
      const gutter = ta.offsetWidth - ta.clientWidth
      mirror.style.paddingRight = gutter > 0 ? (gutter + MIRROR_BLEED) + 'px' : ''
      // 不加偏移：镜像的 padding-top 与负 margin-top 相互抵消，scrollTop 与 textarea 保持同值即可对齐
      mirror.scrollTop = ta.scrollTop
    },
    onInput(event) {
      const ta = (event && event.target) || this.$refs.textareaRef
      // 移动端兜底（见 utils/skillPillTokens.js 的 reconcileTokenDeletion）：Android Chrome 的
      // Backspace 不给 keydown、部分浏览器也没有 beforeinput，浏览器只啃掉标签的一两个字时，
      // 在这里按「编辑前/后文本差分」把整个标签补删干净，避免留下半个标签的残缺文本。
      if (ta && typeof ta.value === 'string') {
        const healed = reconcileTokenDeletion(this.lastInputText, ta.value, ta.selectionStart, this.tokenMap)
        if (healed) {
          this.replaceTextWithCaret(healed)
          this.$emit('typing')
          return
        }
      }
      this.autoResize()
      this.updateCaretLine()
      this.updateSkillPicker()
      this.syncMirror()
      this.$emit('typing')
    },

    /**
     * 写入新文本并把光标放回指定下标（标签插入/删除后的统一收尾）。
     * 等 DOM 更新后再设置光标，避免 v-model 回写把光标顶到末尾；焦点始终留在输入框。
     */
    replaceTextWithCaret(next) {
      const ta = this.$refs.textareaRef
      this.message = next.text
      return this.$nextTick(() => {
        // v-model 可能在本轮 input 里把旧值又写回 message（输入事件的处理顺序由框架决定），
        // 这里再断言一次，保证 ref 与 DOM 都是我们算好的文本，否则后续发送会带上半个标签。
        if (this.message !== next.text) this.message = next.text
        this.autoResize()
        if (ta) {
          if (ta.value !== next.text) ta.value = next.text
          ta.focus()
          ta.setSelectionRange(next.caret, next.caret)
        }
        this.updateCaretLine()
        this.syncMirror()
      })
    },

    /** 记住技能元信息（标签 `/显示名` 要靠它反解技能）：/ 面板与「+」面板的接口都会往这里补 */
    rememberSkills(skills) {
      const incoming = (Array.isArray(skills) ? skills : []).filter((s) => s && s.dir_name)
      if (!incoming.length) return
      const byDir = new Map(this.knownSkills.map((s) => [s.dir_name, s]))
      let changed = false
      incoming.forEach((skill) => {
        const prev = byDir.get(skill.dir_name)
        if (!prev) {
          byDir.set(skill.dir_name, skill)
          changed = true
        } else if ((prev.name || '') !== (skill.name || '')) {
          // 技能改名后，标签要按最新显示名解析
          byDir.set(skill.dir_name, { ...prev, ...skill })
          changed = true
        }
      })
      if (changed) this.knownSkills = Array.from(byDir.values())
    },

    // ========== 技能选择（输入 / 唤起） ==========

    /** 暴露给模板：类别 id → SkillIcon 配色 key */
    iconCategoryFor(categoryId) {
      return iconCategoryFor(categoryId)
    },

    async loadSkillOptions() {
      const now = Date.now()
      // 技能列表变动不频繁，60s 内复用上次结果，避免每次输入 / 都打接口
      if (this.skillOptions.length && now - this.skillLoadedAt < 60000) return
      this.skillPickerLoading = true
      try {
        const data = await getUserSkills()
        // 标签解析要认识所有技能（草稿里可能留着已停用技能的标签），故先登记全量再过滤面板列表
        this.rememberSkills(data.skills || [])
        this.skillOptions = (data.skills || []).filter(s => s.enabled)
        this.skillLoadedAt = now
      } catch (e) {
        console.error('获取技能列表失败:', e)
      } finally {
        this.skillPickerLoading = false
      }
    },

    /** 面板出现在输入框上方（Vue2 无 Teleport，用 fixed 定位脱离 overflow: hidden） */
    updateSkillPickerPosition() {
      const box = this.$refs.inputBoxRef
      if (!box) return
      const rect = box.getBoundingClientRect()
      const margin = 8
      const maxPanelHeight = 320
      const spaceAbove = rect.top - margin * 2
      const spaceBelow = window.innerHeight - rect.bottom - margin * 2
      // 技能装多之后面板会撑到最大高度：若仍按「贴着输入框上沿 + 固定高度」定位，
      // 面板顶部会越过视口，首个技能被裁在屏幕外且怎么滚都够不着。因此改为优先向上
      // 弹出、但把高度限制在输入框上方的可用空间内（下方空间明显更宽裕时才向下弹）。
      const placeAbove = spaceAbove >= 160 || spaceAbove >= spaceBelow
      const available = placeAbove ? spaceAbove : spaceBelow
      const maxHeight = Math.max(96, Math.min(maxPanelHeight, available))
      this.skillPickerStyle = {
        position: 'fixed',
        left: `${rect.left}px`,
        width: `${rect.width}px`,
        maxHeight: `${Math.round(maxHeight)}px`,
        ...(placeAbove
          ? { bottom: `${window.innerHeight - rect.top + margin}px` }
          : { top: `${rect.bottom + margin}px` }),
      }
    },

    /** 键盘切换选项时把当前高亮项滚进可视区（只滚列表本身，不带动外层级） */
    scrollActiveSkillIntoView() {
      const picker = this.$refs.skillPickerRef
      if (!picker) return
      const list = picker.querySelector('.skill-picker-list')
      const item = list && list.children[this.skillPickerIndex]
      if (!list || !item) return
      const top = item.offsetTop
      const bottom = top + item.offsetHeight
      if (top < list.scrollTop) list.scrollTop = top
      else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight
    },

    /** 判定光标前是否为「/查询词」（/ 位于开头或空白之后），决定展开或收起面板 */
    updateSkillPicker() {
      const ta = this.$refs.textareaRef
      if (!ta) return
      const caret = ta.selectionStart === null || ta.selectionStart === undefined
        ? ta.value.length
        : ta.selectionStart
      const slash = parseSlashQuery(ta.value, caret)
      if (!slash) {
        this.closeSkillPicker()
        return
      }

      this.slashStart = slash.start
      this.skillQuery = slash.query
      this.skillPickerIndex = 0
      this.skillPickerOpen = true
      this.updateSkillPickerPosition()
      this.loadSkillOptions()
    },

    closeSkillPicker() {
      this.skillPickerOpen = false
      this.skillPickerIndex = 0
      this.skillQuery = ''
      this.slashStart = -1
    },

    /**
     * 选中技能：删掉刚输入的 /查询词，在光标处插入内联标签 `/显示名`，光标停在标签之后。
     * 已在文本里的同一技能不重复插入（光标落到已有标签之后）。
     */
    async selectSkill(skill) {
      if (!this.skillPickerOpen || !skill || !skill.dir_name) return

      const ta = this.$refs.textareaRef
      const base = (ta && ta.value) || this.message
      const caret = ta && ta.selectionStart !== null && ta.selectionStart !== undefined
        ? ta.selectionStart
        : base.length
      // 以当前光标为准重算一次 /查询词 的范围，避免用户中途改动文本后误删内容
      const slash = parseSlashQuery(base, caret)
      const start = slash ? slash.start : caret
      const cleared = base.slice(0, start) + base.slice(caret)

      this.rememberSkills([skill])
      const existing = skillSegments(cleared, this.tokenMap)
        .find((seg) => seg.dirName === skill.dir_name)
      const next = existing
        ? { text: cleared, caret: existing.end }
        : insertSkillToken(cleared, start, tokenForSkill(skill))

      this.closeSkillPicker()
      await this.replaceTextWithCaret(next)
      console.log(`[${new Date().toISOString()}] [技能选择] ${skill.dir_name}`)
    },

    /** 整段删除技能标签 + 复位光标（Backspace / Delete / 点击标签都走这里） */
    applyTokenRemoval(base, segsToRemove) {
      if (!Array.isArray(segsToRemove) || !segsToRemove.length) return
      this.replaceTextWithCaret(removeTokenSegments(base, segsToRemove))
    },

    /** 点击标签 / 面板里取消选择：把该技能的标签从文本里整段删掉 */
    removeSkillToken(dirName) {
      const ta = this.$refs.textareaRef
      const base = (ta && ta.value) || this.message
      const segs = skillSegments(base, this.tokenMap).filter((seg) => seg.dirName === dirName)
      if (!segs.length) return
      this.applyTokenRemoval(base, segs)
    },

    /**
     * 恢复「待选技能」：技能中心「创建技能」等入口会把技能写进 sessionStorage，
     * 面板关闭后本组件重新挂载时读出来挂成技能标签（读后即清除，避免下次误挂）。
     */
    /**
     * 恢复「待选技能」：技能中心「创建技能」等入口会把技能写进 sessionStorage，
     * 面板关闭后本组件重新挂载时读出来挂成技能标签（读后即清除，避免下次误挂）。
     *
     * `insert_token === false` 时**不插标签**：草稿正文已用自然语言点名技能
     * （如专家面板的「用「expert-creator」技能帮我创建：…」），这里只把技能
     * 记为预选，发送时随 skills 字段交给后端激活。
     */
    restorePendingSkill() {
      let raw = null
      try {
        raw = sessionStorage.getItem(PENDING_SKILL_KEY)
        if (raw) sessionStorage.removeItem(PENDING_SKILL_KEY)
      } catch (_) { /* 隐私模式等场景忽略 */ }
      if (!raw) return

      try {
        const parsed = JSON.parse(raw)
        // 两种载荷：单个技能对象（技能中心）与 {skills: [...]}（专家面板：
        // 新建带 expert-creator，编辑还会带上该专家已绑定的技能包）
        const list = Array.isArray(parsed && parsed.skills)
          ? parsed.skills
          : (parsed ? [parsed] : [])
        for (const skill of list) {
          if (!skill || !skill.dir_name) continue
          this.rememberSkills([skill])
          if (skill.insert_token === false) {
            if (!this.preselectedSkillDirs.includes(skill.dir_name)) {
              this.preselectedSkillDirs.push(skill.dir_name)
            }
            continue
          }
          const ta = this.$refs.textareaRef
          const base = (ta && ta.value) || this.message
          if (skillSegments(base, this.tokenMap).some((seg) => seg.dirName === skill.dir_name)) continue
          this.replaceTextWithCaret(insertSkillToken(base, base.length, tokenForSkill(skill)))
        }
      } catch (e) {
        console.error('恢复待选技能失败:', e)
      }
    },

    /**
     * Backspace / Delete 紧邻标签时整段删除标签（光标在别处则保持常规的逐字符删除）。
     * 光标「紧邻标签之后」= selectionStart === 标签结束下标，与文本是同一套下标，桌面端/移动端一致。
     */
    handleTokenDeleteKey(event) {
      if (event.isComposing) return false
      if (event.key !== 'Backspace' && event.key !== 'Delete') return false
      const ta = event.target
      if (!ta || typeof ta.selectionStart !== 'number') return false
      // 有选区时按常规删除（用户是在删一段选中的文本）
      if (ta.selectionStart !== ta.selectionEnd) return false
      const caret = ta.selectionStart
      const seg = event.key === 'Backspace'
        ? tokenBeforeCaret(ta.value, caret, this.tokenMap)
        : tokenAfterCaret(ta.value, caret, this.tokenMap)
      if (!seg) return false
      event.preventDefault()
      this.applyTokenRemoval(ta.value, [seg])
      return true
    },

    /**
     * beforeinput 路径：移动端（Android Chrome）Backspace 不给 keydown，键盘删除只能在这里拦。
     * 拦截后自行整段删标签；拦不到的情况由 onInput 的差分自愈兜底（见 reconcileTokenDeletion）。
     */
    onBeforeInput(event) {
      if (event.isComposing) return
      const deleteBackward = event.inputType === 'deleteContentBackward'
      const deleteForward = event.inputType === 'deleteContentForward'
      if (!deleteBackward && !deleteForward) return
      const ta = event.target
      if (!ta || typeof ta.selectionStart !== 'number') return
      if (ta.selectionStart !== ta.selectionEnd) return
      const caret = ta.selectionStart
      const seg = deleteBackward
        ? tokenBeforeCaret(ta.value, caret, this.tokenMap)
        : tokenAfterCaret(ta.value, caret, this.tokenMap)
      if (!seg) return
      event.preventDefault()
      this.applyTokenRemoval(ta.value, [seg])
    },

    /** 键盘：面板打开时接管 ↑↓ / Enter / Esc，其余情况保持原有 Enter 发送 */
    onKeydown(event) {
      if (this.handleTokenDeleteKey(event)) return

      if (this.skillPickerOpen) {
        const count = this.filteredSkills.length
        if (event.key === 'ArrowDown' && count > 0) {
          event.preventDefault()
          this.skillPickerIndex = (this.skillPickerIndex + 1) % count
          this.$nextTick(() => this.scrollActiveSkillIntoView())
          return
        }
        if (event.key === 'ArrowUp' && count > 0) {
          event.preventDefault()
          this.skillPickerIndex = (this.skillPickerIndex - 1 + count) % count
          this.$nextTick(() => this.scrollActiveSkillIntoView())
          return
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          this.closeSkillPicker()
          return
        }
        if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
          const skill = this.filteredSkills[this.skillPickerIndex]
          if (skill) {
            event.preventDefault()
            this.selectSkill(skill)
            return
          }
          this.closeSkillPicker()
        }
      }

      if (
        event.key === 'Enter' &&
        !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey &&
        !event.isComposing
      ) {
        event.preventDefault()
        this.send()
      }
    },

    /** 点击面板外的区域时收起技能面板 */
    closeSkillPickerOnClick(event) {
      if (!this.skillPickerOpen) return
      const picker = this.$refs.skillPickerRef
      if (picker && picker.contains(eventTarget(event))) return
      this.closeSkillPicker()
    },

    /** 窗口尺寸变化时重新定位（移动端键盘弹出会触发 resize） */
    handleViewportChange() {
      if (this.skillPickerOpen) this.updateSkillPickerPosition()
    },
  },
}
</script>

<style scoped>
.chat-input-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 24px 4px;
  background: transparent;
  /* 预留与消息区相同的滚动条 gutter，使输入框与消息（含「处理过程」）左右对齐。 */
  overflow-y: auto;
  scrollbar-gutter: stable;
}

.input-box {
  width: 80%;
  max-width: 900px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;
  overflow: hidden;
}

.input-box:focus-within {
  border-color: #0ea5e9;
  box-shadow: 0 2px 12px rgba(14, 165, 233, 0.15);
}

/* ── 文件列表容器：额度行在上、文件卡片换行排布（原来是直接 flex-wrap 卡片） ── */

/* ── 内联技能标签（`/显示名`，发送时整段剥掉，技能走 skills 字段） ── */
/* 镜像渲染层：与 textarea 同字体/字号/行高/宽度，两侧文字换行位置才会逐行一致。
   叠在 textarea 之上但 pointer-events:none，输入、光标、选区仍由下层 textarea 承担；
   break-spaces + break-word 对齐 textarea 的默认换行规则（含行尾空格折行）。 */
.input-mirror {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 2;
  overflow: hidden;
  pointer-events: none;
  /* 裁剪盒向外扩 6px：overflow:hidden 是按 padding 盒裁剪的，而镜像内容正好从 padding 盒
     边缘起步 —— 技能芯片向外扩的 4px 留白在**行首左侧 / 首行上沿 / 末行下沿**会被切平，
     看起来像被切了一刀。padding 撑开裁剪区、再用等量负 margin 把盒子拉回来，内容位置与
     宽度分毫不变（绝对定位 left+right 定宽，padding 与负 margin 相互抵消）。
     注意 syncMirror 里补滚动条占位时要一并加上同样的 6px（MIRROR_BLEED）。 */
  padding: 6px;
  margin: -6px;
  /* translateZ(0)：提升为独立合成层。Chromium 对「超长 inline 内容 + overflow:hidden +
     程序化 scrollTop」的组合存在裁剪渗漏（内容被画到裁剪区外），强制独立层后消失 */
  transform: translateZ(0);
  font-family: 'Microsoft YaHei', '微软雅黑', sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: break-spaces;
  overflow-wrap: break-word;
}

.input-mirror-text {
  white-space: break-spaces;
  overflow-wrap: break-word;
}

/* 内联技能标签：可见文字就是 textarea 文本里的 token（`／显示名`）。
   硬约束（见 utils/skillPillTokens.js）：文字必须逐字等于 textarea 里的子串，且**不参与任何布局宽度** ——
   所以这里只允许动颜色/背景/描边/透明度这类纯绘制属性，绝不能用 font-* / letter-spacing / padding /
   border / margin（会把标签撑宽，导致镜像层与 textarea 换行错位、光标跑偏）。
   视觉留白靠绝对定位的 ::before 向外扩画出来：它是绘制层、不占宽度，
   于是既有芯片的呼吸感（左右各 6px、上下各 4px），又保持与 textarea 逐像素对齐。
   pointer-events 恢复 auto：点击标签（含扩出来的留白）整段移除。
   观感参照参考图的 chip：**浅强调色胶囊（全圆角、无边框）**、
   深色名字 + 同色线条图标（强调色会显得像一串蓝链接）、图标与名字之间留出间隙。 */
.input-skill-pill {
  position: relative;
  font: inherit;
  line-height: inherit;
  color: var(--text-primary);
  pointer-events: auto;
  user-select: none;
  cursor: pointer;
}

/* 芯片底：胶囊（全圆角）+ 浅强调色底、**不描边**，落在文字下层
   （z-index: -1 收进镜像层这个合成层，所以仍在 textarea 之上、在文字之下）。

   四边外扩量都是量出来的：
   · 竖：上下合计 6px → 芯片高 23px，**收在 24px 行高之内**，不会探到相邻行去
     （之前上下各 4px = 25px，比行高还高，观感就是"高度和文字没对齐"）；
     top 比 bottom 多 1.5px（整体上提 0.75px）：实测字心比 span 中心高 0.8~1.3px，
     且拉丁字心比中文字心又高 0.5px —— 提完后两者的偏差一正一负、都在 0.4px 内，
     （再提 0.5px 就轮到芯片偏高 0.67px，所以取这一档）。
  · 右：内收 9px —— 尾随的全角空格（1em ≈ 15px）里，6px 是芯片右内边距，余下 9px 就是
    芯片与后续文字的间隙，后面的字不会再压在芯片上。
  · 左：外扩 6px —— 图标左缘到芯片边缘约 6px 留白；左边有「前置空格 + 全角斜线自带的
    字形留白」可吃，不会压住前一个字。
  · 底与圆角：color-mix(accent 24%, bg-secondary) 作底（此前 12% 太淡、要靠 1px 描边才看得见，
    现在**无边框**，得让底色自己立起来，故加深到 24%，hover 32%）；border-radius: 999px 即胶囊
    （芯片高 23px 时半径被 clamp 到 11.5px；不写死 px，免得将来改高度就露出直角）。 */

.input-skill-pill::before {
  content: '';
  position: absolute;
  inset: -3.75px 9px -2.25px -6px;
  z-index: -1;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-color) 24%, var(--bg-secondary));
  transition: background 0.15s ease-out;
}

/* 紧邻的两个标签：间隔全靠前一个标签尾随的全角空格（15px）。后一个沿用左侧 6px 外扩，
   前一个再多收 2px，两片半透明底之间才有约 5px 的缝（否则只剩 3px，看着会连成一片）。
   :has 不支持时回落到 3px，仍不会重叠。 */
.input-skill-pill:has(+ .input-skill-pill)::before {
  right: 11px;
}


.input-skill-pill:hover::before {
  background: color-mix(in srgb, var(--accent-color) 32%, var(--bg-secondary));
}

/* `／`（全角斜线）占位框：字形必须真的写在 mark 里（镜像文本是 textarea 文本的逐字拷贝，
   少一个字换行/光标就会错位），visibility:hidden 只藏字形、盒子照常按字形步进占位
   （正文 CJK 字体下 ≈ 1 个汉字宽）；图标再用 visibility:visible 画进来。 */
.input-skill-pill-mark {
  position: relative;
  visibility: hidden;
}

/* 图标：绝对定位在斜线占位框内、**左对齐**（不占布局宽度）。左对齐后右边剩下的
   那点空白天然成为图标与名字之间的间隙，不用给标签加 padding。
   尺寸用 em 随字号缩放、不能写死 px，否则换个字号/字体就会压到旁边的文字。
   形态参照参考图：**只有线条图标本身 —— 没有方块底座、没有描边**，颜色与名字同为
   正文色（SkillIcon 在卡片/详情页按分类上色，这里是输入框里的第二形态，所以要压掉
   它的底色与分类色；emoji 图标保持彩色，改不了也不该改）。
   （选择器特意写到三层，压过 SkillIcon 自身 .skill-icon.xs 的尺寸与暗色底规则；
    Vue2 下子组件根节点带父组件的 scope 属性，scoped 选择器可直接命中） */
.input-skill-pill .input-skill-pill-mark .skill-icon {
  position: absolute;
  left: -0.06em;
  /* 与芯片同步上提 0.5px（见 ::before 注释），图标才与字形墨迹同一条中线 */
  top: calc(50% - 0.5px);
  transform: translateY(-50%);
  width: 0.86em;
  height: 0.86em;
  visibility: visible;
  color: var(--text-primary) !important;
  border-radius: 0 !important;
  background: none !important;
  box-shadow: none !important;
}

.input-skill-pill .input-skill-pill-mark .skill-icon svg {
  width: 0.7em;
  height: 0.7em;
}

.input-skill-pill .input-skill-pill-mark .skill-icon .icon-emoji {
  font-size: 0.66em;
  line-height: 1;
}

/* 名字用正文色（浅色 #1e293b / 深色白）：强调色会显得像链接，正文色才像"芯片"。
   深色下 App.vue 的全局 `html[data-theme="dark"] span { color: var(--text-primary) !important }`
   刚好给出同样的结果，两套主题观感一致。 */

/* ── 连接器面板：行内「会话级开关」（只影响本次会话的 Agent 工具集，不写 mcp.json） ── */






/* 提交中：明确禁止态（正常状态下开关任何一端都能点） */


/* 正在提交：开关中心转圈 */

@keyframes connector-switch-spin {
  to {
    transform: rotate(360deg);
  }
}


/* 连接器面板内的操作反馈条（改完开关提示一句） */
.plus-subpanel-notice {
  margin: 6px 10px 0;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.plus-subpanel-notice.error {
  background: rgba(220, 38, 38, 0.08);
  color: #dc2626;
}

html[data-theme="dark"] .plus-subpanel-notice {
  background: #27272a;
  color: #a1a1aa;
}

html[data-theme="dark"] .plus-subpanel-notice.error {
  background: rgba(248, 113, 113, 0.12);
  color: #f87171;
}

html[data-theme="dark"] .connector-switch {
  background: #3f3f46;
  border-color: #52525b;
}

html[data-theme="dark"] .connector-switch.on {
  background: var(--accent-color);
  border-color: var(--accent-color);
}

/* ── 技能选择面板（fixed 定位，脱离 .input-box 的 overflow: hidden） ── */
.skill-picker {
  z-index: 1200;
  display: flex;
  flex-direction: column;
  max-height: min(320px, 42vh);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-primary);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
  overflow: hidden;
}

.skill-picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  font-size: 12px;
  color: var(--text-secondary);
}

.skill-picker-hint {
  font-size: 11px;
  opacity: 0.8;
}

.skill-picker-state {
  padding: 14px 12px;
  font-size: 12.5px;
  color: var(--text-secondary);
  text-align: center;
}

.skill-picker-list {
  /* 面板是定高 flex 容器：列表必须可收缩（min-height: 0）才会在内部滚动，
     否则内容会撑破面板被外层 overflow: hidden 裁掉，滚动条也随之失效 */
  flex: 1 1 auto;
  min-height: 0;
  /* 让列表成为条目的 offsetParent，键盘滚动用的 offsetTop 与 scrollTop 同源 */
  position: relative;
  margin: 0;
  padding: 6px;
  list-style: none;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}

.skill-picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  cursor: pointer;
}

.skill-picker-item.active {
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

.skill-picker-body {
  flex: 1;
  min-width: 0;
  display: flex;
  /* 单行展示：技能名与描述同行，描述超出宽度用省略号截断，避免整条占两行 */
  flex-direction: row;
  align-items: baseline;
  gap: 8px;
}

.skill-picker-name {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-picker-desc {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-picker-slash {
  flex-shrink: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

/* 移动端：面板更紧凑，去掉冗余提示 */
@media (max-width: 48rem) {
  .skill-picker {
    max-height: min(240px, 38vh);
  }

  .skill-picker-hint {
    display: none;
  }
}

/* ── 上传限额提示条 / 额度行 / 重试按钮 ──
   兼容 IE 与旧 Edge：只用了 flex + 圆角，颜色写成「字面量在前、var() 在后」；
   不使用 color-mix、不使用 flex gap。── */
.upload-notice {
  display: flex;
  align-items: center;
  margin: 10px 16px 0;
  padding: 7px 8px 7px 10px;
  border: 1px solid #fcd34d;
  border: 1px solid var(--warning-border, #fcd34d);
  border-radius: 8px;
  background: #fffbeb;
  background: var(--warning-bg, #fffbeb);
}

.upload-notice-text {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: #92400e;
  color: var(--warning-text, #92400e);
  word-break: break-all;
}

.upload-notice-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-left: 8px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #92400e;
  color: var(--warning-text, #92400e);
  cursor: pointer;
  opacity: 0.75;
}

.upload-notice-close:hover {
  opacity: 1;
}

.upload-notice-close svg {
  width: 10px;
  height: 10px;
}



















/* 失败卡片上的「重试」：仅可重试的失败（非单文件超限）才渲染 */


/* × 常驻卡片内部右上角：上传前/上传中/上传完成/失败均可点击删除。
   兼容 Edge 114~155：仅用绝对定位 + 圆角 + 字面量回退，不用新特性。 */



.input-row {
  padding: 12px 16px;
  display: flex;
  /* 顶部对齐：多行输入时胶囊与正文首行齐平（flex-end 会把胶囊压到最后一行） */
  align-items: flex-start;
  gap: 12px;
  /* 技能标签与输入文字同一行；胶囊自身换行，不把输入区挤到下一行 */
  flex-wrap: wrap;
}

.input-field {
  /* flex-basis 0：有技能标签时输入区仍留在同一行（basis 100% 会被 wrap 挤到下一行） */
  flex: 1 1 0;
  width: auto;
  min-width: 120px;
  position: relative;
}

/* 光标所在行高亮条（覆盖在 textarea 下层，仅深色模式可见） */
.caret-line {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 0;
  border-radius: 4px;
  background: transparent;
  pointer-events: none;
}

html[data-theme="dark"] .caret-line {
  /* 光标所在行颜色与 textarea 区域背景一致，点击时不再出现突兀色带 */
  background: var(--bg-tertiary) !important;
}

/* textarea 所在区域使用浅灰底（与全局深色风格一致） */
html[data-theme="dark"] .input-field textarea {
  background: var(--bg-tertiary) !important;
  /* App.vue 的全局深色规则给 textarea 强制 color: var(--text-primary) !important，
     会把镜像层方案依赖的透明文字盖掉（暗色下出现镜像+本体双重文字），这里压回透明 */
  color: transparent !important;
  caret-color: var(--text-primary) !important;
}

/* 整个输入框（容器、操作区、模型选择、上传按钮、textarea、光标行）
   在深色模式下统一为浅灰色，保持视觉一致。 */
html[data-theme="dark"] .input-box {
  background: var(--bg-tertiary) !important;
}

html[data-theme="dark"] .input-actions {
  background: var(--bg-tertiary) !important;
}

/* 深色模式工具按钮：统一深灰底 + 暗色描边（原 #9ca3af 亮灰描边过于抢眼），
   选择器带 .input-actions 前缀是为了压过 App.vue 里针对资产页 .plus-btn 的全局 !important 规则 */
html[data-theme="dark"] .input-actions .model-btn,
html[data-theme="dark"] .input-actions .plus-btn,
html[data-theme="dark"] .input-actions .web-search-btn {
  /* 输入框在深色下就是 --bg-tertiary，按钮同色会糊成一片，
     掺一点主文字色提亮，形成可辨识的「芯片」层级 */
  background: color-mix(in srgb, var(--text-primary) 7%, var(--bg-tertiary)) !important;
  border-color: color-mix(in srgb, var(--text-primary) 14%, var(--border-color)) !important;
  box-shadow: none !important;
}

html[data-theme="dark"] .input-actions .model-btn:hover:not(.disabled),
html[data-theme="dark"] .input-actions .plus-btn:hover:not(.disabled),
html[data-theme="dark"] .input-actions .web-search-btn:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--accent-color) 55%, var(--border-color)) !important;
  background: color-mix(in srgb, var(--accent-color) 16%, var(--bg-tertiary)) !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35) !important;
}

html[data-theme="dark"] .input-actions .model-btn-icon,
html[data-theme="dark"] .input-actions .model-btn-label,
html[data-theme="dark"] .input-actions .model-btn-arrow,
html[data-theme="dark"] .input-actions .plus-btn svg,
html[data-theme="dark"] .input-actions .web-search-btn svg {
  color: var(--text-secondary) !important;
}

html[data-theme="dark"] .input-actions .model-btn:hover:not(.disabled) .model-btn-icon,
html[data-theme="dark"] .input-actions .model-btn:hover:not(.disabled) .model-btn-label,
html[data-theme="dark"] .input-actions .model-btn:hover:not(.disabled) .model-btn-arrow,
html[data-theme="dark"] .input-actions .plus-btn:hover:not(.disabled) svg,
html[data-theme="dark"] .input-actions .web-search-btn:hover:not(:disabled) svg {
  color: var(--accent-color) !important;
}

html[data-theme="dark"] .input-actions .web-search-btn.active {
  border-color: transparent !important;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent-color) 85%, #ffffff),
    var(--accent-color)
  ) !important;
  box-shadow: 0 2px 12px color-mix(in srgb, var(--accent-color) 45%, transparent) !important;
}

html[data-theme="dark"] .input-actions .web-search-btn.active:hover:not(:disabled) {
  border-color: transparent !important;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent-color) 92%, #ffffff),
    var(--accent-color)
  ) !important;
  box-shadow: 0 4px 16px color-mix(in srgb, var(--accent-color) 55%, transparent) !important;
}

html[data-theme="dark"] .input-actions .web-search-btn.active svg {
  color: #ffffff !important;
}

/* token 用量圆环：全局深色规则把环底描边设成 var(--bg-tertiary)，
   与灰色输入框背景同色导致不可见，黑色主题下改为白色圆环 */
html[data-theme="dark"] .context-ring-bg {
  stroke: #ffffff !important;
}

html[data-theme="dark"] .context-ring-wrapper {
  border: 1px solid #ffffff !important;
  border-radius: 50% !important;
  box-sizing: border-box !important;
}

.input-field textarea {
  position: relative;
  z-index: 1;
  width: 100%;
  border: none;
  background: transparent;
  resize: none;
  font-family: 'Microsoft YaHei', '微软雅黑', sans-serif;
  font-size: 15px;
  line-height: 1.6;
  /* 文字由上层的 .input-mirror 镜像层显示，这里只保留光标与选区（见 syncMirror） */
  color: transparent;
  caret-color: var(--text-primary);
  /* 最多 5 行（行高 24px）：文本更长时在右侧出现滚动条，输入框不再无限增高 */
  max-height: 120px;
  overflow-y: auto;
  min-height: 24px;
  padding: 0;
  outline: none;
}

/* 选区高亮画在下层 textarea，镜像文字叠在其上，选中文字仍然可读 */
.input-field textarea::selection {
  background: color-mix(in srgb, var(--accent-color) 28%, transparent);
}

.input-field textarea::placeholder {
  color: var(--text-secondary);
  font-size: 14px;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 12px;
  border-top: none;
  background: transparent;
}

.left-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.plus-menu-wrapper {
  display: inline-flex;
}











/* 键盘可达性：工具按钮共用一致的焦点环 */
.plus-btn:focus-visible,
.web-search-btn:focus-visible,
.send-btn:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 2px;
}





/* ========== 「+」添加菜单 ========== */








html[data-theme="dark"] .plus-menu {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
}

html[data-theme="dark"] .plus-menu-item:hover {
  background: color-mix(in srgb, var(--text-primary) 8%, transparent);
}

/* 二级列表视图 */











/* 可点选行（技能 / 连接器）：加手型 + 已选态整行高亮（再点一次取消） */


/* 停用技能行不可点：Agent 只加载已启用的技能，挂上去也不会生效 */


/* 行尾「已选」标：技能 / 连接器共用，与专家行的召唤态样式一致 */
















html[data-theme="dark"] .plus-list-item:hover {
  background: color-mix(in srgb, var(--text-primary) 8%, transparent);
}

html[data-theme="dark"] .plus-list-tag.on {
  background: rgba(22, 163, 74, 0.2);
  color: #86efac;
}

html[data-theme="dark"] .plus-list-tag.custom {
  background: rgba(124, 58, 237, 0.2);
  color: #c4b5fd;
}



.right-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.action-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
}

.action-btn:hover {
  background: var(--bg-tertiary);
}

/* 联网搜索按钮：点亮（图标变蓝）= 启用；灰色 = 未启用。
   启用态不加背景色块，仅图标高亮；未配置时半透明且屏蔽 hover/点击的一切高亮反馈。 */
.web-search-btn.active {
  background: transparent;
}
.web-search-btn.active svg {
  color: #0ea5e9;
}
.web-search-btn.unavailable {
  opacity: 0.45;
  cursor: pointer;
}
.web-search-btn.unavailable:hover {
  background: transparent;
}
.web-search-btn.unavailable:hover svg {
  color: var(--text-secondary);
}
.web-search-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.action-btn:hover svg {
  color: #0ea5e9;
}

.send-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: var(--bg-tertiary);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.send-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
  transition: all 0.2s;
}

.send-btn.active {
  background: #0ea5e9;
}

.send-btn.active svg {
  color: white;
}

.send-btn.active:hover:not(:disabled) {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.3);
}

.send-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.stop-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: #ef4444;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stop-btn svg {
  width: 16px;
  height: 16px;
  color: white;
}

.stop-btn:hover {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
}

.context-ring-wrapper {
  position: relative;
  width: 36px;
  height: 36px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.context-ring-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.2s;
}

.context-ring-wrapper:hover .context-ring-inner {
  transform: scale(1.1);
}

.context-ring {
  width: 36px;
  height: 36px;
}

.context-ring-bg {
  stroke: var(--border-color);
}

.context-ring-text {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 9px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: 1;
  pointer-events: none;
}

.token-popup {
  position: fixed;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 14px 16px;
  min-width: 200px;
  z-index: 9999;
  cursor: default;
}

.token-popup-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.token-popup-section {
  margin-bottom: 2px;
}

.token-popup-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
}

.token-popup-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.token-popup-value {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

/* 语义色：11px 小字需满足 WCAG AA 4.5:1（对白色背景）。
   原 indigo-500 / cyan-500 / purple-500 仅 4.46:1 / 2.43:1 / 3.92:1，
   均不达标，故整体加深到 600~700 档（6.2~7.0:1） */
.token-popup-value.input {
  color: #4f46e5;
}

.token-popup-value.output {
  color: #0e7490;
}

.token-popup-value.reasoning {
  color: #7e22ce;
}

.token-popup-value.duration-value {
  color: #4f46e5;
  font-weight: 600;
}

.token-popup-context-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 2px 0 4px;
}

.token-popup-context-value {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.token-popup-context-percent {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.token-popup-divider {
  height: 1px;
  background: var(--bg-tertiary);
  margin: 6px 0;
}

.token-popup-bar {
  margin-top: 4px;
}

.token-popup-bar-inner {
  height: 5px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-tertiary);
}

.token-popup-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}

/* 上传附件 / 联网搜索：与模型选择同规格的图标按钮，
   hover 统一走主题强调色（此前上传紫、搜索蓝两套 hover 色不一致） */
.plus-btn,
.web-search-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.plus-btn svg,
.web-search-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.plus-btn:hover:not(.disabled),
.web-search-btn:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--accent-color) 45%, var(--border-color));
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-secondary));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--accent-color) 18%, transparent);
  transform: translateY(-1px);
}

.plus-btn:hover:not(.disabled) svg,
.web-search-btn:hover:not(:disabled) svg {
  color: var(--accent-color);
  transform: scale(1.08);
}

.plus-btn:active:not(.disabled),
.web-search-btn:active:not(:disabled) {
  transform: translateY(0) scale(0.96);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}

.plus-btn.disabled,
.web-search-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── 当前专家胶囊：与左侧工具按钮同排同高，首字头像 + 名称 + 退出×。
   边框/底色完全照搬左侧按钮（model-btn）的方案：纯 var()，不用 color-mix ——
   用户 Edge 上 color-mix 计算异常（边框发黑）。IE 回退：字面量值在前。── */
.expert-chip {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 8px 0 6px;
  max-width: 220px;
  border: 1px solid #e2e8f0;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: #ffffff;
  background: var(--bg-secondary);
  flex-shrink: 0;
}

.expert-chip-avatar {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 按专家 ID 取档（见 style.css），此处只留尺寸 */
  font-size: 12px;
  font-weight: 600;
  line-height: 22px;
  text-align: center;
  user-select: none;
}

.expert-chip-name {
  min-width: 0;
  margin: 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: #0ea5e9;
  color: var(--accent-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expert-chip-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #0ea5e9;
  color: var(--accent-color);
  cursor: pointer;
  opacity: 0.7;
  transition: opacity 0.2s ease, background 0.2s ease;
}

.expert-chip-clear:hover:not(:disabled) {
  opacity: 1;
  background: #f1f5f9;
  background: var(--bg-tertiary);
}

.expert-chip-clear:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.expert-chip-clear:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 1px;
}

.expert-chip-clear svg {
  width: 10px;
  height: 10px;
}

.copy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.copy-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
  transition: all 0.2s;
}

.copy-btn:hover:not(.disabled) {
  background: var(--bg-tertiary);
}

.copy-btn:hover:not(.disabled) svg {
  color: #0ea5e9;
}

.copy-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-footer {
  margin-top: 2px;
  text-align: center;
}

.footer-text {
  font-size: 12px;
  color: var(--text-secondary);
}



</style>

<style>
/* ========== 「+」菜单二级浮动面板（主菜单常驻） ========== */
.plus-subpanel {
  /* 与 ChatInput.vue 里的 SUBPANEL_WIDTH 常量同源（单行布局需要更宽的面板） */
  width: 480px;
  max-width: 90vw;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary, #fff);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 12px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.14);
  padding: 12px;
  z-index: 9999;
}
/* 热桥：面板与一级菜单之间有 6px 水平间隙，鼠标横移时两边都不属于，会先触发
   mouseleave 计时。向左延 7px（覆盖间隙 + 1px 重叠）把这段空白划给面板。
   面板底边最低与一级菜单底边平齐（内容少了会往上收），所以再向下延 28px 把
   「右上角接缝」也划进面板，从菜单项斜着往左上角挪时不会中途掉出去。 */
.plus-subpanel::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: -28px;
  left: -7px;
  width: 7px;
}
.plus-subpanel-list {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
  /* 高度策略：列表不自带上限，交给面板统一控制——
     面板的 height 由 syncSubpanelBox 按内容自然高度算出（封顶见 SUBPANEL_MAX_HEIGHT），
     列表 flex 撑满剩余空间，超了才滚动。搜索后条数变少 → 面板变矮、底边上收
     （最低与一级菜单底边齐平，最高收到与菜单顶边齐平）。 */
  flex: 1 1 auto;
  min-height: 0;
}
/* 空态 / 无结果提示：面板高度被锁定时列表区可能很高，单条提示靠垂直居中显示，
   看起来才像「有意的空状态」而不是「内容没加载出来」。 */
.plus-subpanel-list > .plus-list-hint:only-child,
.plus-subpanel-list > .skill-sub-hint:only-child {
  margin: auto 0;
}
/* 头部（标题+计数+搜索）/ 底栏不参与压缩，只有列表可伸缩：
   面板的 height 由 syncSubpanelBox 按内容内联给出，内容超上限时必须先压列表。 */
.plus-subpanel > .plus-subpanel-header,
.plus-subpanel > .plus-subpanel-head,
.plus-subpanel > .plus-sub-search,
.plus-subpanel > .plus-subpanel-footer {
  flex: 0 0 auto;
}
.plus-subpanel-footer {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border-color, #e2e8f0);
}
.plus-subpanel-manage {
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 13px;
  /* <button> 不继承字体，显式声明与面板内其它文字同源 */
  font-family: inherit;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
  text-align: center;
}
.plus-subpanel-manage:hover {
  background: rgba(15, 23, 42, 0.05);
}

/* ── 专家面板：上区「我的专家」+ 浅色分隔线 + 下区「公共专家」── */
/* 高度与技能/连接器面板一致：不再单独限高，由 syncSubpanelBox 统一给面板算。
   两区各 6 行（plusExpertsLimit）刚好铺满，滚动到底再放出下一批。 */
/* 已召唤：整行高亮（再点一次取消） */

/* 头像配色已收敛到全局 .ea-0 ~ .ea-7（style.css + utils/expertAvatar.js）：
   按 export_agent_id 稳定取档，技能中心 / 下拉 / 胶囊 / 消息标识四处同色。
   这里不再保留按序号取色的 av-c0..av-c7，避免两套色板并存。 */
/* 默认与提示文字同色，悬停才变强调色（不做成显眼的链接） */
/* 「还有 N 位」兜底行（内容未超高、无滚动条时的可达入口） */
</style>
