<template>
  <div class="chat-input-container">
    <div class="input-box" ref="inputBoxRef">
      <div v-if="uploadedFiles.length > 0" class="uploaded-files">
        <div 
          v-for="(file, index) in uploadedFiles" 
          :key="index" 
          class="uploaded-file"
        >
          <div class="file-icon">
            <FileIcon :filename="file.filename" :size="36" />
          </div>
          <div class="file-info">
            <span class="file-name">{{ file.filename }}</span>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <div v-if="file.uploadStatus === 'uploading'" class="upload-progress">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: file.uploadProgress + '%' }"></div>
                </div>
                <span class="progress-text">{{ file.uploadProgress }}%</span>
              </div>
              <span v-else-if="file.uploadStatus === 'completed'" class="upload-status completed">上传完成</span>
              <span v-else-if="file.uploadStatus === 'error'" class="upload-status error">上传失败</span>
            </div>
          </div>
          <button @click="removeFile(index)" class="remove-file-btn">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </div>
      
      <div class="input-row">
        <!-- 已选技能：与输入文字同一行展示（只显示图标 + 名称，发送时还原为 /技能名） -->
        <div v-if="selectedSkills.length > 0" class="selected-skills">
          <div
            v-for="skill in selectedSkills"
            :key="skill.dir_name"
            class="selected-skill"
            :title="skill.description || skill.dir_name"
          >
            <SkillIcon :icon="skill.icon" :category="iconCategoryFor(skill.category_id)" size="xs" />
            <span class="selected-skill-name">{{ skill.name || skill.dir_name }}</span>
            <button
              type="button"
              class="selected-skill-remove"
              :aria-label="`移除技能 ${skill.name || skill.dir_name}`"
              @click="removeSelectedSkill(skill)"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        <div class="input-field">
          <div
            v-show="caretLineTop !== null"
            class="caret-line"
            :style="{ top: caretLineTop + 'px', height: lineHeight + 'px' }"
          ></div>
          <textarea
            ref="textareaRef"
            v-model="message"
            v-tip:hover="'输入消息，Enter 发送 · Shift+Enter 换行 · 输入 / 唤起技能选择'"
            @keydown="onKeydown"
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
              v-tip="'添加文件 · 专家 · 技能 · 连接器'"
              aria-haspopup="menu"
              :aria-expanded="showPlusMenu ? 'true' : 'false'"
              @click="togglePlusMenu"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
            <div v-if="showPlusMenu" class="plus-menu" :style="plusMenuStyle" @click.stop @mouseenter="cancelPlusClose" @mouseleave="schedulePlusClose">
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('file')" @click="handleMenuAddFile">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="12" y1="18" x2="12" y2="12"></line>
                    <line x1="9" y1="15" x2="15" y2="15"></line>
                  </svg>
                  <span class="plus-menu-label">添加文件</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('experts')" @click="openSubpanel('experts', $event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                    <circle cx="12" cy="7" r="4"></circle>
                  </svg>
                  <span class="plus-menu-label">专家</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('skills')" @click="openSubpanel('skills', $event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                  </svg>
                  <span class="plus-menu-label">技能</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('connectors')" @click="openSubpanel('connectors', $event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 22v-5"></path>
                    <path d="M9 8V2"></path>
                    <path d="M15 8V2"></path>
                    <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"></path>
                  </svg>
                  <span class="plus-menu-label">连接器</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>

            <!-- 「+」菜单二级浮动面板：主菜单常驻，子面板显示在触发项旁边。
                 专家 = 上下两区（我的专家 / 公共专家）；技能 / 连接器 = 用户条目列表。
                 悬停或点击触发，互斥显示。 -->
            <div v-if="subpanelCategory" class="plus-subpanel" :style="plusSubpanelStyle" @click.stop @mouseenter="cancelPlusClose" @mouseleave="schedulePlusClose">
              <div class="plus-subpanel-head">
                <span class="plus-subpanel-title">{{ plusListTitle }}</span>
                <span class="plus-menu-count">{{ plusListCount }}</span>
              </div>

              <!-- 专家：上下两区（我的专家 / 公共专家），点击行 = 召唤（再点 = 取消召唤） -->
              <div
                v-if="!plusListLoading && !plusListError && subpanelCategory === 'experts'"
                class="plus-subpanel-list plus-experts"
                @scroll="onExpertsScroll"
              >
                <!-- 上区：我的专家（仅展示「可用」状态的专家，与专家 Tab 的召唤约束一致） -->
                <div class="plus-experts-section">
                  <span class="plus-experts-section-label">我的专家</span>
                  <span v-if="myExpertsActive.length" class="plus-experts-section-count">{{ myExpertsActive.length }}</span>
                </div>
                <button
                  v-for="(item, idx) in visibleMyExperts"
                  :key="`mine:${item.export_agent_id}`"
                  type="button"
                  class="plus-expert-row"
                  :class="{ selected: isExpertSelected(item) }"
                  :title="isExpertSelected(item) ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
                  @click="toggleExpert(item)"
                >
                  <span class="plus-expert-avatar" :class="'av-c' + (idx % 8)">{{ expertAvatar(item) }}</span>
                  <span class="plus-expert-main">
                    <span class="plus-expert-name">{{ item.export_name_cn }}</span>
                    <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
                  </span>
                  <span v-if="isExpertSelected(item)" class="plus-expert-state">已选</span>
                </button>
                <div v-if="!myExpertsActive.length" class="plus-expert-empty">
                  <span class="plus-expert-empty-text">您还没有创建自己的专家</span>
                  <button type="button" class="plus-expert-create" @click="handleMenuPanel('experts')">
                    「去创建」
                  </button>
                </div>
                <!-- 兜底：内容未超出容器高度时不会产生滚动条，用可点击的「还有 N 位」保证可达 -->
                <button
                  v-if="myExpertsActive.length > visibleMyExperts.length"
                  type="button"
                  class="plus-expert-more"
                  @click="loadMoreExperts"
                >
                  还有 {{ myExpertsActive.length - visibleMyExperts.length }} 位 · 滚动或点击加载
                </button>

                <div class="plus-experts-divider"></div>

                <!-- 下区：公共（预置）专家 -->
                <div class="plus-experts-section">
                  <span class="plus-experts-section-label">公共专家</span>
                  <span class="plus-experts-badge">预置</span>
                  <span v-if="publicExperts.length" class="plus-experts-section-count">{{ publicExperts.length }}</span>
                </div>
                <button
                  v-for="(item, idx) in visiblePublicExperts"
                  :key="`public:${item.export_agent_id}`"
                  type="button"
                  class="plus-expert-row"
                  :class="{ selected: isExpertSelected(item) }"
                  :title="isExpertSelected(item) ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
                  @click="toggleExpert(item)"
                >
                  <span class="plus-expert-avatar" :class="'av-c' + (idx % 8)">{{ expertAvatar(item) }}</span>
                  <span class="plus-expert-main">
                    <span class="plus-expert-name">{{ item.export_name_cn }}</span>
                    <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
                  </span>
                  <span v-if="isExpertSelected(item)" class="plus-expert-state">已选</span>
                </button>
                <div v-if="!publicExperts.length" class="plus-list-hint">公共专家目录为空</div>
                <button
                  v-if="publicExperts.length > visiblePublicExperts.length"
                  type="button"
                  class="plus-expert-more"
                  @click="loadMoreExperts"
                >
                  还有 {{ publicExperts.length - visiblePublicExperts.length }} 位 · 滚动或点击加载
                </button>
              </div>

              <!-- 技能 / 连接器：沿用通用条目列表 -->
              <div v-else class="plus-subpanel-list">
                <div v-if="plusListLoading" class="plus-list-hint">加载中…</div>
                <div v-else-if="plusListError" class="plus-list-hint error">{{ plusListError }}</div>
                <div v-else-if="plusList.length === 0" class="plus-list-hint">{{ plusListEmptyText }}</div>
                <div v-else v-for="item in plusList" :key="item.key" class="plus-list-item">
                  <span class="plus-list-icon" :class="{ initial: !item.icon }">{{ item.icon || item.initial }}</span>
                  <span class="plus-list-body">
                    <span class="plus-list-name-row">
                      <span class="plus-list-name">{{ item.name }}</span>
                      <span v-if="item.tag" class="plus-list-tag" :class="item.tagType">{{ item.tag }}</span>
                    </span>
                    <span class="plus-list-desc">{{ item.desc }}</span>
                  </span>
                </div>
              </div>
              <div class="plus-subpanel-footer">
                <button type="button" class="plus-subpanel-manage" @click="handleMenuPanel(subpanelCategory)">
                  在管理面板中打开 →
                </button>
              </div>
            </div>
            </div>
          </div>
          <input
            ref="fileInputRef"
            type="file"
            @change="handleFileSelect"
            multiple
            :disabled="isStreaming || disabled"
            hidden
          />


        <div class="model-dropdown-wrapper" ref="modelDropdownRef">
            <button
              class="model-btn"
              :class="{ disabled: isStreaming || disabled }"
              :disabled="isStreaming || disabled"
              @click="toggleModelDropdown"
              v-tip="'切换模型'"
            >
              <svg class="model-btn-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path>
                <path d="M18.5 15.5l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9z"></path>
              </svg>
              <span class="model-btn-label">{{ currentModelLabel }}</span>
              <svg class="model-btn-arrow" :class="{ open: showModelDropdown }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </button>
              <div v-if="showModelDropdown" class="model-dropdown-menu" :style="dropdownStyle" @click.stop>
                <div
                  v-for="m in models"
                  :key="m.name"
                  class="model-dropdown-item"
                  :class="{ active: m.name === localSelectedModel }"
                  @click="selectModel(m.name)"
                >
                  <span class="model-item-name">{{ m.model || m.name }}</span>
                  <svg v-if="m.name === localSelectedModel" class="model-item-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                </div>
              </div>
          </div>

          <button
            type="button"
            class="action-btn web-search-btn"
            :class="{ active: webSearchEnabled, disabled: webSearchUnavailable || isStreaming || disabled }"
            :disabled="webSearchUnavailable || isStreaming || disabled"
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
            v-if="activeExpert"
            class="expert-chip"
            :title="`当前专家：${activeExpert.export_name_cn}（点击 × 退出专家模式）`"
          >
            <span class="expert-chip-avatar">{{ expertAvatar(activeExpert) }}</span>
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
import { uploadFile, deleteFile } from '../api/files.js'
import { getWebSearchStatus } from '../api/settings.js'
import { getUserSkills } from '../api/skills.js'
import { iconCategoryFor } from '../utils/skillCategories.js'
import { buildSkillPayload, parseSlashQuery } from '../utils/skillSlash.js'
import { getConnectors } from '../api/connectors.js'
import { eventTarget } from '../utils/domEvent.js'
import { listExperts, listPublicExperts } from '../api/expert.js'
import FileIcon from './FileIcon.vue'
import SkillIcon from './SkillIcon.vue'

// 输入草稿持久化：页面刷新后恢复用户尚未发送的输入内容（“输入框的状态不变”）
const DRAFT_KEY = 'easy_agent_input_draft'

// 待选技能：由技能中心「创建技能」等入口写入，本组件挂载时恢复成技能标签
const PENDING_SKILL_KEY = 'easy_agent_pending_skill'

export default {
  components: { FileIcon, SkillIcon },
  props: {
    disabled: {
      type: Boolean,
      default: false
    },
    sessionId: {
      type: String,
      default: null
    },
    isStreaming: {
      type: Boolean,
      default: false
    },
    sessionUsage: {
      type: Object,
      default: () => ({ input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_length: null, auto_compress_tokens: null, context_tokens: 0 })
    },
    sessionDuration: {
      type: Number,
      default: 0
    },
    iterationCount: {
      type: Number,
      default: 0
    },
    models: {
      type: Array,
      default: () => []
    },
    selectedModel: {
      type: String,
      default: null
    },
    showFooter: {
      type: Boolean,
      default: false
    },
    draftKey: {
      type: String,
      default: DRAFT_KEY
    },
    // 是否消费技能中心「创建技能」预置的待选技能（只应由主页面输入框消费）
    consumePendingSkill: {
      type: Boolean,
      default: true
    },
    // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
    // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/stream_export_run。
    activeExpert: {
      type: Object,
      default: null
    },
    // 外部预填的问题（如专家详情弹窗点「专家帮你做」示例）：非空时写入输入框，
    // 写入后立即 $emit('expert-prompt-applied') 通知父组件清空，避免重复触发。
    expertPrompt: {
      type: String,
      default: ''
    }
  },
  data() {
    return {
      message: '',
      uploadedFiles: [],
      // 光标所在行高亮（overlay 技术：textareal 本身无法按行上色）
      caretLineTop: null, // 高亮条 top，null 表示隐藏
      lineHeight: 24, // 行高（px）
      // 自定义下拉菜单
      showModelDropdown: false,
      showPlusMenu: false,
      subpanelCategory: null,
      plusSubpanelStyle: {},
      plusHoverTimer: null,
      plusListCategory: '',
      plusList: [],
      plusListLoading: false,
      plusListError: '',
      // ========== 专家快捷面板（「+」→ 专家）==========
      // 上区：本人创建且「可用」的专家；下区：内置公共（预置）专家。
      myExperts: [],
      publicExperts: [],
      // 两个区块默认各展示 3 位，列表滚动到底部再加载 3 位，避免面板超高
      plusExpertsLimit: 3,
      plusMenuStyle: {},
      dropdownStyle: {},
      showTokenPopup: false,
      // ========== 联网搜索 ==========
      // 默认关闭；未配置（api_key 缺失/功能关闭）时按钮置灰
      webSearchEnabled: false,
      webSearchStatus: { enabled: false, configured: false },
      // ========== 会话耗时 ==========
      liveDuration: 0,
      durationTimer: null,
      // ========== 技能选择（输入 / 唤起） ==========
      // 输入框里只展示「工具图标 + 技能名」，实际发给模型的是 /技能目录名 + 用户输入。
      // 只有启用状态的技能会被 Agent 加载，停用技能即使手打 /name 也不生效，故只列启用的。
      selectedSkills: [],
      skillPickerOpen: false,
      skillPickerIndex: 0,
      skillPickerLoading: false,
      skillOptions: [],
      skillQuery: '',
      slashStart: -1,
      skillPickerStyle: {},
      skillLoadedAt: 0,
    }
  },
  computed: {
    plusListTitle() {
      const meta = { experts: '专家', skills: '我的技能', connectors: '我的连接器' }
      return meta[this.plusListCategory] || ''
    },
    /** 标题右侧计数：专家面板 = 我的（可用）+ 公共；其余面板 = 列表条数 */
    plusListCount() {
      if (this.plusListCategory === 'experts') {
        return this.myExpertsActive.length + this.publicExperts.length
      }
      return this.plusList.length
    },
    /** 我的专家：仅保留「可用」（active）状态，与专家 Tab 的召唤约束一致 */
    myExpertsActive() {
      return this.myExperts.filter((item) => item && item.agent_status === 'active')
    },
    /** 上区当前可见条目（默认 3 条，滚动加载更多） */
    visibleMyExperts() {
      return this.myExpertsActive.slice(0, this.plusExpertsLimit)
    },
    /** 下区当前可见条目（默认 3 条，滚动加载更多） */
    visiblePublicExperts() {
      return this.publicExperts.slice(0, this.plusExpertsLimit)
    },
    plusListEmptyText() {
      const meta = {
        experts: '暂无专家，敬请期待',
        skills: '还没有自己的技能，可在「专家·技能·连接器」面板添加',
        connectors: '还没有已添加的连接器，可在「专家·技能·连接器」面板添加',
      }
      return meta[this.plusListCategory] || '暂无内容'
    },
    // 模型选择：本地双向绑定，变化时同步父组件
    localSelectedModel: {
      get() {
        return this.selectedModel
      },
      set(val) {
        this.$emit('update:selectedModel', val)
      }
    },
    currentModelLabel() {
      const m = this.models.find(m => m.name === this.selectedModel)
      // 显示模型名（deepseek-v4-flash / deepseek-v4-pro），
      // 而不是 provider 配置段名（deepseek / ark）
      return m ? (m.model || m.name) : '选择模型'
    },
    canSend() {
      return this.message.trim() || this.uploadedFiles.length > 0 || this.selectedSkills.length > 0
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
    webSearchUnavailable() {
      return !this.webSearchStatus.configured
    },
    webSearchTitle() {
      if (!this.webSearchStatus.configured) {
        return '联网搜索未配置（需配置 WEB_SEARCH_API_KEY 后重启后端）'
      }
      return this.webSearchEnabled ? '联网搜索已开启，点击关闭' : '联网搜索（点击开启）'
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
    shouldCountLive(on) {
      if (on) this.startLiveDuration()
      else this.stopLiveDuration()
    },
    message(val) {
      try {
        sessionStorage.setItem(this.draftKey, val)
      } catch (e) {
        // 隐私模式等场景下 sessionStorage 可能不可用，忽略即可
      }
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
    // 恢复待选技能（技能中心「创建技能」等入口预置），读取后即清除
    if (this.consumePendingSkill) this.restorePendingSkill()

    document.addEventListener('click', this.closeTokenPopup)
    document.addEventListener('click', this.closeModelDropdown)
    document.addEventListener('click', this.closeSkillPickerOnClick)
    window.addEventListener('resize', this.handleViewportChange)
    document.addEventListener('click', this.closePlusMenu)
    this.loadWebSearchStatus()
    // 点示例问题会先关闭 SkillCenter 面板 → Chat/ChatInput 重新挂载，
    // 此时 expertPrompt 初始值即非空；watch 只响应"变化"，挂载时的初始值不会触发，
    // 因此在这里补一次（放在草稿恢复之后，确保示例问题覆盖草稿）。
    if (this.expertPrompt) this.applyExpertPrompt(this.expertPrompt)
  },
  beforeDestroy() {
    document.removeEventListener('click', this.closeTokenPopup)
    document.removeEventListener('click', this.closeModelDropdown)
    document.removeEventListener('click', this.closeSkillPickerOnClick)
    window.removeEventListener('resize', this.handleViewportChange)
    document.removeEventListener('click', this.closePlusMenu)
    this.cancelPlusClose()
    this.stopLiveDuration()
  },
  methods: {
    /** 供父组件调用：聚焦输入框并把光标移到末尾 */
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
    /**
     * 外部预填问题（专家详情弹窗的示例提示词）：写入输入框并聚焦，随后通知父组件清空。
     */
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
    updateCaretLine() {
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
    toggleWebSearch() {
      if (this.webSearchUnavailable || this.isStreaming || this.disabled) return
      this.webSearchEnabled = !this.webSearchEnabled
      console.log(
        `[${new Date().toISOString()}] [联网搜索] ${this.webSearchEnabled ? '已开启' : '已关闭'}`
      )
    },
    async loadWebSearchStatus() {
      try {
        const data = await getWebSearchStatus()
        this.webSearchStatus = {
          enabled: !!data.enabled,
          configured: !!data.configured,
        }
        if (!data.configured) this.webSearchEnabled = false
      } catch (e) {
        // 状态获取失败按「不可用」处理，避免用户开启后静默不生效
        console.error('获取联网搜索状态失败:', e)
      }
    },
    toggleModelDropdown() {
      if (this.isStreaming || this.disabled) return
      if (this.showModelDropdown) {
        this.showModelDropdown = false
        return
      }
      // 计算下拉菜单位置
      const anchor = this.$refs.modelDropdownRef
      if (anchor) {
        const rect = anchor.getBoundingClientRect()
        this.dropdownStyle = {
          position: 'fixed',
          bottom: `${window.innerHeight - rect.top + 6}px`,
          left: `${rect.left}px`,
          minWidth: `${Math.max(rect.width, 200)}px`,
        }
      }
      this.showModelDropdown = true
    },
    selectModel(name) {
      this.$emit('update:selectedModel', name)
      this.showModelDropdown = false
      console.log(
        `[${new Date().toISOString()}] [模型选择] 切换为: ${name}`
      )
    },
    closeModelDropdown(event) {
      const anchor = this.$refs.modelDropdownRef
      if (this.showModelDropdown && anchor && !anchor.contains(eventTarget(event))) {
        this.showModelDropdown = false
      }
    },
    togglePlusMenu() {
      if (this.isStreaming || this.disabled) return
      if (this.showPlusMenu) {
        this.showPlusMenu = false
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
    },
    closePlusMenu(event) {
      const anchor = this.$refs.plusMenuRef
      if (this.showPlusMenu && anchor && !anchor.contains(eventTarget(event))) {
        this.showPlusMenu = false
        this.subpanelCategory = null
      }
    },
    handleMenuAddFile() {
      this.showPlusMenu = false
      this.subpanelCategory = null
      const input = this.$refs.fileInputRef
      if (input) input.click()
    },
    // 二级列表：展示当前用户自己的条目（不跳转页面）
    // 数据来源均为当前用户：技能 = user-skills（含已停用）；连接器 = 用户 mcp.json
    // 中已安装的内置连接器 + 自定义服务；专家暂无数据。
    openPlusList(category) {
      this.plusListCategory = category
      this.plusListLoading = true
      this.plusListError = ''
      this.plusList = []
      // 每次打开都重新拉取（接口很轻，且公共专家目录是服务端实时扫描的）
      this.myExperts = []
      this.publicExperts = []
      this.plusExpertsLimit = 3
      this.loadPlusList(category)
    },
    // 打开二级浮动面板（主菜单常驻）：定位到触发项右侧，底部超视口时上移
    openSubpanel(category, event) {
      // click 触发同面板已打开则翻转关闭；hover 触发保持
      if (this.subpanelCategory === category) return
      this.subpanelCategory = category
      const rect = event.currentTarget.getBoundingClientRect()
      const estimatedHeight = 420
      const top = Math.max(8, Math.min(rect.top - 8, window.innerHeight - estimatedHeight - 8))
      const left = rect.right + 10 + 380 > window.innerWidth ? Math.max(8, rect.left - 390) : rect.right + 10
      this.plusSubpanelStyle = {
        position: 'fixed',
        top: `${top}px`,
        left: `${left}px`,
      }
      this.openPlusList(category)
    },
    // ========== 悬停触发二级面板 ==========
    // 鼠标悬停主菜单项即切换对应面板，无需点击；点击仍保留（触屏/键盘回退）。
    // 鼠标离开菜单区域超过 250ms 才整体关闭。
    cancelPlusClose() {
      if (this.plusHoverTimer) {
        clearTimeout(this.plusHoverTimer)
        this.plusHoverTimer = null
      }
    },
    schedulePlusClose() {
      this.cancelPlusClose()
      this.plusHoverTimer = setTimeout(() => {
        this.plusHoverTimer = null
        this.showPlusMenu = false
        this.subpanelCategory = null
      }, 250)
    },
    hoverPlusItem(category) {
      this.cancelPlusClose()
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
          this.plusList = (data.skills || []).map((s) => ({
            key: `skill:${s.dir_name}`,
            name: s.name,
            desc: s.description || '暂无描述',
            icon: s.icon || '',
            initial: (s.name || s.dir_name).slice(0, 1).toUpperCase(),
            tag: s.enabled ? '已启用' : '已停用',
            tagType: s.enabled ? 'on' : 'off',
          }))
        } else if (category === 'connectors') {
          const data = await getConnectors()
          const installed = (data.connectors || [])
            .filter((c) => c.installed)
            .map((c) => ({
              key: `connector:${c.id}`,
              name: c.name,
              desc: c.description || '暂无描述',
              icon: '',
              initial: (c.name || c.id).slice(0, 1).toUpperCase(),
              tag: '已添加',
              tagType: 'on',
            }))
          const custom = (data.custom || []).map((c) => ({
            key: `custom:${c.name}`,
            name: c.name,
            desc: '用户自定义 MCP 服务',
            icon: '',
            initial: (c.name || '').slice(0, 1).toUpperCase(),
            tag: '自定义',
            tagType: 'custom',
          }))
          this.plusList = installed.concat(custom)
        } else {
          this.plusList = []
        }
      } catch (e) {
        this.plusListError = (e && e.message) || '加载失败'
      } finally {
        this.plusListLoading = false
      }
    },
    // ========== 专家快捷面板：行渲染与选择 ==========
    /** 头像取专家名首字（与专家 Tab 的 avatarChar 一致） */
    expertAvatar(item) {
      const name = String((item && item.export_name_cn) || '').trim()
      return name ? name.slice(0, 1) : '?'
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
    /** 每区多放出 3 条（滚动到底 / 点「还有 N 位」都会走到这里） */
    loadMoreExperts() {
      const total = Math.max(this.myExpertsActive.length, this.publicExperts.length)
      if (this.plusExpertsLimit < total) this.plusExpertsLimit += 3
    },
    // 列表底部入口：跳到「专家·技能·连接器」面板做完整管理
    handleMenuPanel(tab) {
      this.showPlusMenu = false
      this.subpanelCategory = null
      this.$emit('openPanel', tab)
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
    async handleFileSelect(event) {
      const files = Array.from(event.target.files)

      for (const file of files) {
        // 创建文件项
        const fileItem = {
          file: file,
          filename: file.name,
          size: file.size,
          uploadStatus: 'uploading',
          uploadProgress: 0
        }

        // 添加到数组
        this.uploadedFiles.push(fileItem)

        // 强制触发初始渲染
        this.uploadedFiles = [...this.uploadedFiles]

        try {
          // 确保会话存在
          let sessionId = this.sessionId
          if (!sessionId) {
            // 通知父组件创建会话
            const sessionIdPromise = new Promise((resolve) => {
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
              // 触发会话创建
              this.$emit('create-session')
            })

            // 等待会话创建完成
            sessionId = await sessionIdPromise

            if (!sessionId) {
              throw new Error('会话创建失败')
            }
          }

          // 调用实际的上传接口
          const response = await uploadFile(sessionId, file, (progress) => {
            // 找到对应的文件项并更新进度
            const index = this.uploadedFiles.findIndex(f => f.file === file)
            if (index !== -1) {
              this.uploadedFiles[index].uploadProgress = progress
              // 强制触发更新
              this.uploadedFiles = [...this.uploadedFiles]
            }
          })

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
          // 找到对应的文件项并更新状态
          const index = this.uploadedFiles.findIndex(f => f.file === file)
          if (index !== -1) {
            this.uploadedFiles[index].uploadStatus = 'error'
            // 强制触发更新
            this.uploadedFiles = [...this.uploadedFiles]
          }
          console.error('文件上传失败:', error)
        }
      }

      event.target.value = ''
    },
    async removeFile(index) {
      const file = this.uploadedFiles[index]

      // 如果文件已经上传成功，调用后台的删除接口
      if (file && file.uploadStatus === 'completed' && file.id && this.sessionId) {
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

      const filesToSend = this.uploadedFiles.map(f => ({
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

      this.$emit(
        'send',
        this.buildPayload(),
        filesToSend,
        null,
        true,
        this.webSearchEnabled
      )

      this.message = ''
      this.uploadedFiles = []
      this.selectedSkills = []
      this.closeSkillPicker()
      this.$nextTick(() => this.autoResize())
    },

    /**
     * 实际发送给模型的内容：技能指令前缀 + 用户输入。
     * 例：界面显示 [图标]PPT生成 +「生成一份PPT创作文档」→ 发送 `/pptx-generator 生成一份PPT创作文档`
     */
    buildPayload() {
      return buildSkillPayload(this.selectedSkills, this.message)
    },
    stop() {
      this.$emit('stop')
    },
    autoResize() {
      const ta = this.$refs.textareaRef
      if (ta) {
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 150) + 'px'
      }
    },
    onInput() {
      this.autoResize()
      this.updateCaretLine()
      this.updateSkillPicker()
      this.$emit('typing')
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

    /** 选中技能：删掉刚输入的 /查询词，并把它挂成输入框里的技能标签 */
    async selectSkill(skill) {
      if (!this.skillPickerOpen || !skill) return

      const ta = this.$refs.textareaRef
      const caret = ta && ta.selectionStart !== null && ta.selectionStart !== undefined
        ? ta.selectionStart
        : this.message.length
      // 以当前光标为准重算一次 /查询词 的范围，避免用户中途改动文本后误删内容
      const slash = parseSlashQuery(this.message, caret)
      const start = slash ? slash.start : caret
      this.message = this.message.slice(0, start) + this.message.slice(caret)

      if (!this.selectedSkills.some(s => s.dir_name === skill.dir_name)) {
        this.selectedSkills = [...this.selectedSkills, skill]
      }

      this.closeSkillPicker()
      await this.$nextTick()
      this.autoResize()
      if (ta) {
        const pos = Math.min(start, this.message.length)
        ta.focus()
        ta.setSelectionRange(pos, pos)
      }
      this.updateCaretLine()
      console.log(`[${new Date().toISOString()}] [技能选择] ${skill.dir_name}`)
    },

    removeSelectedSkill(skill) {
      this.selectedSkills = this.selectedSkills.filter(s => s.dir_name !== skill.dir_name)
    },

    /**
     * 恢复「待选技能」：技能中心「创建技能」等入口会把技能写进 sessionStorage，
     * 面板关闭后本组件重新挂载时读出来挂成技能标签（读后即清除，避免下次误挂）。
     */
    restorePendingSkill() {
      let raw = null
      try {
        raw = sessionStorage.getItem(PENDING_SKILL_KEY)
        if (raw) sessionStorage.removeItem(PENDING_SKILL_KEY)
      } catch (_) { /* 隐私模式等场景忽略 */ }
      if (!raw) return

      try {
        const skill = JSON.parse(raw)
        const exists = this.selectedSkills.some(s => s.dir_name === skill.dir_name)
        if (skill && skill.dir_name && !exists) {
          this.selectedSkills = [...this.selectedSkills, skill]
          this.$nextTick(() => {
            this.autoResize()
            const ta = this.$refs.textareaRef
            if (ta) ta.focus()
          })
        }
      } catch (e) {
        console.error('恢复待选技能失败:', e)
      }
    },

    /** 键盘：面板打开时接管 ↑↓ / Enter / Esc，其余情况保持原有 Enter 发送 */
    onKeydown(event) {
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

.uploaded-files {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
}

/* ── 已选技能标签（只显示图标 + 名称，发送时还原为 /技能名） ── */
.selected-skills {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  max-width: 100%;
  padding-bottom: 3px;
}

.selected-skill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px 3px 4px;
  border: 1px solid color-mix(in srgb, var(--accent-color) 35%, var(--border-color));
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-color) 10%, transparent);
  max-width: 100%;
}

.selected-skill-name {
  font-size: 12.5px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}

.selected-skill-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.selected-skill-remove:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.selected-skill-remove svg {
  width: 11px;
  height: 11px;
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
  flex-direction: column;
  gap: 2px;
}

.skill-picker-name {
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-picker-desc {
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

.uploaded-file {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  padding: 10px 14px;
  border-radius: 12px;
  max-width: 250px;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
}

.uploaded-file:hover {
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  border-color: var(--border-color);
}

.file-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-size {
  font-size: 11px;
  color: var(--text-secondary);
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: 6px;
}

.progress-bar {
  width: 60px;
  height: 4px;
  background: var(--bg-tertiary);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #0ea5e9, #06b6d4);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 10px;
  color: var(--text-secondary);
  min-width: 35px;
}

.upload-status {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 8px;
}

.upload-status.completed {
  color: #15803d;
  background: #dcfce7;
}

.upload-status.error {
  color: #b91c1c;
  background: #fee2e2;
}

.remove-file-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: var(--bg-tertiary);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.remove-file-btn:hover {
  background: var(--border-color);
  transform: scale(1.05);
}

.remove-file-btn svg {
  width: 14px;
  height: 14px;
  color: var(--text-secondary);
}

.remove-file-btn:hover svg {
  color: #dc2626;
}

.input-row {
  padding: 12px 16px;
  display: flex;
  align-items: flex-end;
  gap: 12px;
  /* 技能标签与输入文字同一行；窄屏放不下时标签才换到上一行 */
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
  color: var(--text-primary);
  max-height: 150px;
  min-height: 24px;
  padding: 0;
  outline: none;
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
  gap: 10px;
}

.model-dropdown-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.model-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 10px 0 11px;
  max-width: 220px;
  height: 36px;
  min-height: 36px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
  cursor: pointer;
  transition: background 0.22s ease, border-color 0.22s ease, box-shadow 0.22s ease,
    transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}

/* 星形图标：给「模型选择」一个视觉锚点，避免三个按钮里只有它是纯文字 */
.model-btn-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--accent-color);
  transition: transform 0.25s ease;
}

.model-btn:hover:not(.disabled) {
  border-color: color-mix(in srgb, var(--accent-color) 45%, var(--border-color));
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-secondary));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--accent-color) 18%, transparent);
  transform: translateY(-1px);
}

.model-btn:hover:not(.disabled) .model-btn-icon {
  transform: rotate(-15deg) scale(1.12);
}

.model-btn:active:not(.disabled) {
  transform: translateY(0) scale(0.98);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}

.model-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-btn-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  transition: color 0.22s ease;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-btn:hover:not(.disabled) .model-btn-label {
  color: var(--accent-color);
}

.model-btn-arrow {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  color: var(--text-secondary);
  transition: transform 0.2s ease, color 0.22s ease;
}

.model-btn:hover:not(.disabled) .model-btn-arrow {
  color: var(--accent-color);
}

.model-btn-arrow.open {
  transform: rotate(180deg);
}

/* 键盘可达性：工具按钮共用一致的焦点环 */
.model-btn:focus-visible,
.plus-btn:focus-visible,
.web-search-btn:focus-visible,
.send-btn:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 2px;
}

.model-dropdown-menu {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  z-index: 9999;
  max-height: 320px;
  overflow-y: auto;
}

.model-dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.model-dropdown-item:hover {
  background: var(--bg-tertiary);
}

.model-dropdown-item.active {
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

/* ========== 「+」添加菜单 ========== */
.plus-menu {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  z-index: 9999;
}

.plus-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.plus-menu-item:hover {
  background: var(--bg-tertiary);
}

.plus-menu-item:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: -2px;
}

.plus-menu-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.plus-menu-label {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.plus-menu-item:hover .plus-menu-icon {
  color: var(--accent-color);
}

.plus-menu-arrow {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--text-secondary);
  opacity: 0.7;
}

html[data-theme="dark"] .plus-menu {
  background: var(--bg-tertiary);
  border-color: var(--border-color);
}

html[data-theme="dark"] .plus-menu-item:hover {
  background: color-mix(in srgb, var(--text-primary) 8%, transparent);
}

/* 二级列表视图 */
.plus-menu.is-list {
  width: 300px;
}

.plus-menu-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 8px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 4px;
}

.plus-menu-back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-menu-back:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.plus-menu-back svg {
  width: 14px;
  height: 14px;
}

.plus-menu-back:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 1px;
}

.plus-menu-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.plus-menu-count {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.plus-menu-list {
  max-height: 280px;
  overflow-y: auto;
}

.plus-list-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  transition: background 0.15s ease;
}

.plus-list-item:hover {
  background: var(--bg-tertiary);
}

.plus-list-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  font-size: 14px;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
}

.plus-list-icon.initial {
  font-size: 12px;
  font-weight: 700;
  color: var(--accent-color);
}

.plus-list-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.plus-list-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.plus-list-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-list-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
}

.plus-list-tag.on {
  background: #dcfce7;
  color: #16a34a;
}

.plus-list-tag.off {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.plus-list-tag.custom {
  background: #ede9fe;
  color: #7c3aed;
}

.plus-list-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.plus-list-hint {
  padding: 16px 10px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.plus-list-hint.error {
  color: #dc2626;
}

.plus-menu-footer {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px 2px;
  border: none;
  border-top: 1px solid var(--border-color);
  background: transparent;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
  cursor: pointer;
  transition: color 0.15s ease;
}

.plus-menu-footer:hover {
  color: var(--accent-color);
}

.plus-menu-footer:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: -1px;
  border-radius: 6px;
}

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

.model-item-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.model-item-check {
  width: 16px;
  height: 16px;
  color: #0ea5e9;
  flex-shrink: 0;
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
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
  cursor: pointer;
  transition: background 0.22s ease, border-color 0.22s ease, box-shadow 0.22s ease,
    transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}

.plus-btn svg,
.web-search-btn svg {
  width: 18px;
  height: 18px;
  color: var(--text-secondary);
  transition: color 0.22s ease, transform 0.22s ease;
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

/* 开启态：主题强调色填充 + 光晕（浅色蓝 / 深色紫），与「发送」激活态呼应 */
.web-search-btn.active {
  border-color: transparent;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent-color) 85%, #ffffff),
    var(--accent-color)
  );
  box-shadow: 0 2px 10px color-mix(in srgb, var(--accent-color) 35%, transparent);
}

.web-search-btn.active svg {
  color: #ffffff;
  transform: none;
}

/* 开启态 hover 仍保持实心填充，避免被上面的通用 hover 规则洗成半透明 */
.web-search-btn.active:hover:not(:disabled) {
  border-color: transparent;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--accent-color) 92%, #ffffff),
    var(--accent-color)
  );
  box-shadow: 0 4px 14px color-mix(in srgb, var(--accent-color) 45%, transparent);
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
  background: #0ea5e9;
  background: var(--accent-color);
  color: #fff;
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
  width: 380px;
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
.plus-subpanel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 8px;
}
.plus-subpanel-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}
.plus-subpanel-list {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
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
  color: var(--text-secondary, #64748b);
  cursor: pointer;
  text-align: center;
}
.plus-subpanel-manage:hover {
  background: rgba(15, 23, 42, 0.05);
}

/* ── 专家面板：上区「我的专家」+ 浅色分隔线 + 下区「公共专家」── */
/* 容纳 3 + 3 行（约 320px）：超出则滚动，滚到底自动放出下一批 */
.plus-subpanel-list.plus-experts {
  max-height: 320px;
}
.plus-experts-section {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 6px;
}
.plus-experts-section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
}
.plus-experts-section-count {
  font-size: 11px;
  color: var(--text-secondary, #64748b);
  font-variant-numeric: tabular-nums;
}
.plus-experts-badge {
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 10.5px;
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary, #64748b);
}
.plus-experts-divider {
  height: 1px;
  margin: 8px 4px;
  background: var(--border-color, #e2e8f0);
}
.plus-expert-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.plus-expert-row:hover {
  background: var(--bg-tertiary, #f1f5f9);
}
/* 已召唤：整行高亮（再点一次取消） */
.plus-expert-row.selected {
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
}
.plus-expert-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border-radius: 8px;
  font-size: 12.5px;
  font-weight: 800;
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary, #64748b);
}

/* 头像调色板：按列表序号取色（av-c0..av-c7），相邻行色相跳变大、不会出现
   同色相邻；列表超过 8 位时循环复用。底色取 200 级深色、字取 800 级深色，
   与右侧常规字重的专家名称拉开层次。暗色主题单独配底/字色。 */
.plus-expert-avatar.av-c0 { background: #b5d4f4; color: #0c447c; }
.plus-expert-avatar.av-c1 { background: #fac775; color: #633806; }
.plus-expert-avatar.av-c2 { background: #f4c0d1; color: #72243e; }
.plus-expert-avatar.av-c3 { background: #c0dd97; color: #27500a; }
.plus-expert-avatar.av-c4 { background: #cecbf6; color: #3c3489; }
.plus-expert-avatar.av-c5 { background: #9fe1cb; color: #085041; }
.plus-expert-avatar.av-c6 { background: #f7c1c1; color: #791f1f; }
.plus-expert-avatar.av-c7 { background: #a8d8e8; color: #07495c; }

html[data-theme="dark"] .plus-expert-avatar.av-c0 { background: #1f4a7a; color: #cbe2fa; }
html[data-theme="dark"] .plus-expert-avatar.av-c1 { background: #6b4a12; color: #ffd68a; }
html[data-theme="dark"] .plus-expert-avatar.av-c2 { background: #6b2a45; color: #f7c3d6; }
html[data-theme="dark"] .plus-expert-avatar.av-c3 { background: #35590e; color: #d3eb9f; }
html[data-theme="dark"] .plus-expert-avatar.av-c4 { background: #3a3390; color: #cfc9fa; }
html[data-theme="dark"] .plus-expert-avatar.av-c5 { background: #0f5f4a; color: #9fe7cf; }
html[data-theme="dark"] .plus-expert-avatar.av-c6 { background: #6b2222; color: #f5b8b8; }
html[data-theme="dark"] .plus-expert-avatar.av-c7 { background: #12556b; color: #a3dbeb; }
.plus-expert-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.plus-expert-name {
  flex-shrink: 0;
  max-width: 52%;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #1e293b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.plus-expert-desc {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--text-secondary, #94a3b8);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.plus-expert-state {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-color, #0ea5e9);
  color: #fff;
}
.plus-expert-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 8px;
  font-size: 12.5px;
  color: var(--text-secondary, #64748b);
}
/* 默认与提示文字同色，悬停才变强调色（不做成显眼的链接） */
.plus-expert-create {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary, #64748b);
  cursor: pointer;
}
.plus-expert-create:hover {
  color: var(--accent-color, #0ea5e9);
  text-decoration: underline;
}
/* 「还有 N 位」兜底行（内容未超高、无滚动条时的可达入口） */
.plus-expert-more {
  width: 100%;
  margin-top: 2px;
  padding: 5px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 11.5px;
  color: var(--text-secondary, #64748b);
  text-align: center;
  cursor: pointer;
}
.plus-expert-more:hover {
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--accent-color, #0ea5e9);
}
</style>
