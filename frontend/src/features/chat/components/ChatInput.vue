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
               点击标签同样整段移除（标签里不放 ×，避免改变宽度）。 -->
          <div v-show="mirrorText" ref="mirrorRef" class="input-mirror" aria-hidden="true"
            ><template v-for="(seg, index) in mirrorSegments" :key="index"
              ><span v-if="seg.type === 'text'" class="input-mirror-text">{{ seg.text }}</span
              ><span
                v-else
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
            v-tip.hover="'输入消息，Enter 发送 · Shift+Enter 换行 · 输入 / 唤起技能选择'"
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
            <!-- 「+」添加菜单：添加文件 / 专家 / 技能 / 连接器（参考同类输入框的 + 交互） -->
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
            <Teleport to="body">
              <div v-if="showPlusMenu" ref="plusMenuListRef" class="plus-menu" :style="plusMenuStyle" @click.stop @mouseenter="cancelPlusClose" @mouseleave="schedulePlusClose">
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
                <button v-if="knowledgeEnabled" type="button" class="plus-menu-item plus-menu-item-knowledge" @mouseenter="hoverPlusItem('knowledge', $event)" @click="openKnowledgeScope($event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
                    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
                  </svg>
                  <span class="plus-menu-label">知识库</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('experts', $event)" @click="openSubpanel('experts', $event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                    <circle cx="12" cy="7" r="4"></circle>
                  </svg>
                  <span class="plus-menu-label">专家</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" @mouseenter="hoverPlusItem('skills', $event)" @click="openSubpanel('skills', $event)">
                  <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                  </svg>
                  <span class="plus-menu-label">技能</span>
                  <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button type="button" class="plus-menu-item" data-category="connectors" @mouseenter="hoverPlusItem('connectors', $event)" @click="openSubpanel('connectors', $event)">
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
              </div>
            </Teleport>

            <!-- 知识库引用：加号菜单「知识库」悬停/点击展开，
                 面板与技能子面板同款定位，锚到「知识库」菜单项右侧 -->
            <KnowledgeScopeSelector
              ref="knowledgeScope"
              :session-id="sessionId || ''"
              :disabled="disabled || isStreaming"
              hide-trigger
              anchor-selector=".plus-menu-item-knowledge"
              @create-session="emit('createSession')"
              @manage="openKnowledgeManage"
              @mouseenter="cancelPlusClose"
              @mouseleave="schedulePlusClose"
            />

            <!-- 「+」菜单二级浮动面板：主菜单常驻，子面板显示在触发项旁边。
                 skills → 技能列表（搜索/本地导入/管理入口，点击插入技能令牌）；
                 experts / connectors → 对应的用户条目列表。悬停或点击触发，互斥显示。 -->
            <Teleport to="body">
              <!-- 技能：外框必须由 ChatInput 自己渲染（原生 div），与专家 / 连接器完全同构。
                   早前把外框放在 SkillPicker 组件内部，位置高度靠 prop 传入，导致
                   syncSubpanelBox 量不到真实高度、搜索后收缩不生效。 -->
              <div
                v-if="subpanelCategory === 'skills'"
                ref="subpanelEl"
                class="skill-submenu plus-subpanel"
                :style="skillSubmenuStyle"
                @click.stop
                @mouseenter="onSubpanelEnter"
                @mouseleave="onSubpanelLeave"
                @focusin="onSubpanelFocusIn"
                @focusout="onSubpanelFocusOut"
              >
                <SkillPicker
                  :items="filteredSkillList"
                  :selected-dirs="selectedSkillDirs"
                  :loading="skillListLoading"
                  :error="skillListError"
                  :search="skillSearch"
                  :title="plusListTitle"
                  :count="plusListCount"
                  @update:search="skillSearch = $event"
                  @select="onSkillSubItemClick"
                  @import="handleSkillImport"
                  @manage="handleMenuPanel('skills')"
                />
              </div>

              <!-- 专家：上下两区（我的专家 / 公共专家），点行 = 召唤，再点 = 取消召唤 -->
              <div
                v-else-if="subpanelCategory === 'experts'"
                ref="subpanelEl"
                class="skill-submenu plus-subpanel"
                :style="skillSubmenuStyle"
                @click.stop
                @mouseenter="onSubpanelEnter"
                @mouseleave="onSubpanelLeave"
                @focusin="onSubpanelFocusIn"
                @focusout="onSubpanelFocusOut"
              >
                <div class="plus-subpanel-head">
                  <span class="plus-subpanel-title">{{ plusListTitle }}</span>
                  <span class="plus-menu-count">{{ plusListCount }}</span>
                </div>
                <!-- 搜索框常驻（与技能 / 连接器同一形态），按名称 / 描述 / 依赖技能名过滤 -->
                <div
                  v-if="hasPanelItems && !plusListLoading && !plusListError"
                  class="skill-sub-search"
                >
                  <svg class="skill-sub-search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                  </svg>
                  <input
                    v-model="subpanelKeyword"
                    type="text"
                    :placeholder="plusListSearchPlaceholder"
                    :aria-label="plusListSearchPlaceholder"
                    class="skill-sub-search-input"
                    spellcheck="false"
                    autocomplete="off"
                    @keydown.esc.stop.prevent="subpanelKeyword = ''"
                  />
                  <button
                    v-if="subpanelKeyword"
                    type="button"
                    class="skill-sub-search-clear"
                    aria-label="清空搜索"
                    @click="subpanelKeyword = ''"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
                <div v-if="plusListLoading" class="skill-sub-hint">加载中…</div>
                <div v-else-if="plusListError" class="skill-sub-hint error">{{ plusListError }}</div>
                <div v-else class="skill-sub-list plus-experts" :class="{ stacked: compact }" @scroll="onExpertsScroll">
                  <!-- 上区：我的专家（仅「可用」状态，与专家 Tab 的召唤约束一致）。
                       有搜索词时清空的区连标题一起收起，避免「标题 + 空白」的空壳。 -->
                  <div v-if="showMineSection" class="plus-experts-section">
                    <span class="plus-experts-section-label">我的专家</span>
                    <span v-if="filteredMyExperts.length" class="plus-experts-section-count">{{ filteredMyExperts.length }}</span>
                  </div>
                  <button
                    v-for="item in visibleMyExperts"
                    :key="`mine:${item.export_agent_id}`"
                    type="button"
                    class="plus-expert-row"
                    :class="{ selected: isExpertSelected(item) }"
                    :title="isExpertSelected(item) ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
                    @click="toggleExpert(item)"
                  >
                    <span class="plus-expert-avatar" :class="expertAvatarClass(item)">{{ expertAvatarChar(item) }}</span>
                    <span class="plus-expert-main">
                      <span class="plus-expert-name-row">
                        <span class="plus-expert-name">{{ item.export_name_cn }}</span>
                        <span v-if="compact && isExpertSelected(item)" class="plus-expert-state">已选</span>
                      </span>
                      <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
                    </span>
                    <span v-if="!compact && isExpertSelected(item)" class="plus-expert-state">已选</span>
                  </button>
                  <div v-if="!subpanelKeyword && !filteredMyExperts.length" class="plus-expert-empty">
                    <span class="plus-expert-empty-text">您还没有创建自己的专家</span>
                    <button type="button" class="plus-expert-create" @click="handleMenuPanel('experts')">
                      「去创建」
                    </button>
                  </div>
                  <!-- 兜底：内容未超出容器高度时不会产生滚动条，用可点击的「还有 N 位」保证可达 -->
                  <button
                    v-if="filteredMyExperts.length > visibleMyExperts.length"
                    type="button"
                    class="plus-expert-more"
                    @click="loadMoreExperts"
                  >
                    还有 {{ filteredMyExperts.length - visibleMyExperts.length }} 位 · 点击加载
                  </button>

                  <div v-if="showMineSection && showPublicSection" class="plus-experts-divider"></div>

                  <!-- 下区：公共（预置）专家 -->
                  <div v-if="showPublicSection" class="plus-experts-section">
                    <span class="plus-experts-section-label">公共专家</span>
                    <span class="plus-experts-badge">预置</span>
                    <span v-if="filteredPublicExperts.length" class="plus-experts-section-count">{{ filteredPublicExperts.length }}</span>
                  </div>
                  <button
                    v-for="item in visiblePublicExperts"
                    :key="`public:${item.export_agent_id}`"
                    type="button"
                    class="plus-expert-row"
                    :class="{ selected: isExpertSelected(item) }"
                    :title="isExpertSelected(item) ? '再次点击取消召唤' : `召唤「${item.export_name_cn}」`"
                    @click="toggleExpert(item)"
                  >
                    <span class="plus-expert-avatar" :class="expertAvatarClass(item)">{{ expertAvatarChar(item) }}</span>
                    <span class="plus-expert-main">
                      <span class="plus-expert-name-row">
                        <span class="plus-expert-name">{{ item.export_name_cn }}</span>
                        <span v-if="compact && isExpertSelected(item)" class="plus-expert-state">已选</span>
                      </span>
                      <span class="plus-expert-desc">{{ expertDesc(item) }}</span>
                    </span>
                    <span v-if="!compact && isExpertSelected(item)" class="plus-expert-state">已选</span>
                  </button>
                  <div v-if="!subpanelKeyword && !filteredPublicExperts.length" class="skill-sub-hint">公共专家目录为空</div>
                  <!-- 搜索无命中：两区都空时给一句统一提示 -->
                  <div
                    v-if="subpanelKeyword && !filteredMyExperts.length && !filteredPublicExperts.length"
                    class="skill-sub-hint"
                  >
                    未找到匹配的专家
                  </div>
                  <button
                    v-if="filteredPublicExperts.length > visiblePublicExperts.length"
                    type="button"
                    class="plus-expert-more"
                    @click="loadMoreExperts"
                  >
                    还有 {{ filteredPublicExperts.length - visiblePublicExperts.length }} 位 · 点击加载
                  </button>
                </div>
                <div v-if="showManageEntry" class="skill-sub-footer">
                  <button type="button" class="skill-sub-footer-btn" @click="handleMenuPanel('experts')">
                    <span>在管理面板中打开 →</span>
                  </button>
                </div>
              </div>

              <!-- 连接器：用户条目列表（与技能面板同款浮层交互，主菜单常驻）。
                   行右侧是「会话级开关」：只影响本次会话的 Agent 工具集，不写 mcp.json。 -->
              <div
                v-else-if="subpanelCategory === 'connectors'"
                ref="subpanelEl"
                class="skill-submenu plus-subpanel"
                :style="skillSubmenuStyle"
                @click.stop
                @mouseenter="onSubpanelEnter"
                @mouseleave="onSubpanelLeave"
                @focusin="onSubpanelFocusIn"
                @focusout="onSubpanelFocusOut"
              >
                <div class="plus-subpanel-head">
                  <span class="plus-subpanel-title">{{ plusListTitle }}</span>
                  <span class="plus-menu-count">{{ plusListCount }}</span>
                </div>
                <!-- 搜索框常驻（专家 / 技能 / 连接器同一形态），输入即过滤 -->
                <div
                  v-if="hasPanelItems && !plusListLoading && !plusListError"
                  class="skill-sub-search"
                >
                  <svg class="skill-sub-search-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                  </svg>
                  <input
                    v-model="subpanelKeyword"
                    type="text"
                    :placeholder="plusListSearchPlaceholder"
                    :aria-label="plusListSearchPlaceholder"
                    class="skill-sub-search-input"
                    spellcheck="false"
                    autocomplete="off"
                    @keydown.esc.stop.prevent="subpanelKeyword = ''"
                  />
                  <button
                    v-if="subpanelKeyword"
                    type="button"
                    class="skill-sub-search-clear"
                    aria-label="清空搜索"
                    @click="subpanelKeyword = ''"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
                <div class="skill-sub-list">
                  <div v-if="plusListLoading" class="skill-sub-hint">加载中…</div>
                  <div v-else-if="plusListError" class="skill-sub-hint error">{{ plusListError }}</div>
                  <div v-else-if="plusList.length === 0" class="skill-sub-hint">{{ plusListEmptyText }}</div>
                  <div v-else-if="filteredPlusList.length === 0" class="skill-sub-hint">未找到匹配的连接器</div>
                  <div
                    v-else
                    v-for="item in filteredPlusList"
                    :key="item.key"
                    class="plus-list-item connector-row"
                  >
                    <span class="plus-list-icon" :class="{ initial: !item.icon }">{{ item.icon || item.initial }}</span>
                    <span class="plus-list-body">
                      <span class="plus-list-name">{{ item.name }}</span>
                      <span v-if="item.tag" class="plus-list-tag" :class="item.tagType">{{ item.tag }}</span>
                      <span class="plus-list-desc">{{ item.desc }}</span>
                    </span>
                    <button
                      type="button"
                      role="switch"
                      class="connector-switch"
                      :class="{
                        on: isConnectorOn(item),
                        'is-off': isConnectorSwitchDisabled(item),
                        'is-busy': isConnectorBusy(item),
                      }"
                      :disabled="isConnectorSwitchDisabled(item)"
                      :aria-checked="isConnectorOn(item) ? 'true' : 'false'"
                      :aria-busy="isConnectorBusy(item) ? 'true' : 'false'"
                      :aria-label="connectorSwitchLabel(item)"
                      :title="connectorSwitchTitle(item)"
                      @click.stop="toggleConnectorSwitch(item)"
                    >
                      <span v-if="isConnectorBusy(item)" class="connector-switch-spinner"></span>
                      <span class="connector-switch-knob"></span>
                    </button>
                  </div>
                </div>
                <!-- 操作反馈：刚改过的连接器开关提示一句，并说明已同步到「专家·技能·连接器」 -->
                <div
                  v-if="connectorNotice"
                  class="skill-sub-notice"
                  :class="connectorNoticeType"
                  role="status"
                  aria-live="polite"
                >
                  {{ connectorNotice }}
                </div>
                <div v-if="showManageEntry" class="skill-sub-footer">
                  <button type="button" class="skill-sub-footer-btn" @click="handleMenuPanel(subpanelCategory)">
                    <span>在管理面板中打开 →</span>
                  </button>
                </div>
              </div>
            </Teleport>

            <input
              ref="skillImportInputRef"
              type="file"
              accept=".zip"
              hidden
              @change="handleSkillImportFile"
            />
          </div>
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
            @update:selected-model="onModelChange"
          />

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
               与专家 Tab 的「使用」联动，点击 × 退出专家模式回到通用 Agent。
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
              @click="emit('clearExpert')"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
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
            <svg class="context-ring" viewBox="0 0 36 36">
              <circle class="context-ring-bg" cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" stroke-width="3" />
              <circle class="context-ring-fill" cx="18" cy="18" r="15.9" fill="none"
                :stroke="contextColor" stroke-width="3" stroke-linecap="round"
                :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
                transform="rotate(-90 18 18)" />
            </svg>
            <span class="context-ring-text">{{ contextPercent }}%</span>
            <Teleport to="body">
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
            </Teleport>
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

    <!-- 技能选择面板：输入 / 后出现在输入框上方（Teleport 到 body，避免被输入框裁切） -->
    <Teleport to="body">
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
    </Teleport>

    <div v-if="showFooter" class="input-footer">
      <span class="footer-text">内容由AI生成，请仔细甄别</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { uploadFile, deleteFile } from '../../../api/files.js'
import { getKnowledgeStatus, getWebSearchStatus, getUploadLimits } from '../../../api/settings.js'
import { getUserSkills, importSkill } from '../../../api/skills.js'
import { iconCategoryFor } from '../../../utils/skillCategories.js'

// 镜像层为画出技能芯片的外扩留白，把裁剪盒向外扩了这么多 px（见 .input-mirror 的 padding/margin）：
// 补滚动条占位时要一起加上，否则镜像内容比 textarea 窄、换行位置对不上。
const MIRROR_BLEED = 6
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
import { listExperts, listPublicExperts } from '../../../api/expert.js'
import { expertAvatarChar, expertAvatarClass } from '../../../utils/expertAvatar.js'
import { formatSize } from '../../../utils/format.js'
import SkillPicker from './SkillPicker.vue'
import UploadTray from './UploadTray.vue'
import ModelPicker from './ModelPicker.vue'
import KnowledgeScopeSelector from '../../knowledge/KnowledgeScopeSelector.vue'
import FileIcon from '../../../shared/components/FileIcon.vue'
import SkillIcon from '../../../shared/components/SkillIcon.vue'

// 输入框最大高度：5 行（行高 1.6 × 15px = 24px）。文本再长就在框内滚动（右侧滚动条），
// 输入框不再无限增高——与 CSS 的 max-height 保持一致，两处改要一起改。
const TEXTAREA_MAX_ROWS = 5
const TEXTAREA_LINE_HEIGHT = 24
const TEXTAREA_MAX_HEIGHT = TEXTAREA_MAX_ROWS * TEXTAREA_LINE_HEIGHT

const props = defineProps({
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
  // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
  // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/chat/stream（带 expert_agent_id）。
  activeExpert: {
    type: Object,
    default: null
  },
  // 外部预填的问题（如专家详情弹窗点「专家帮你做」示例）：非空时写入输入框，
  // 写入后立即 emit('prefillApplied') 通知父组件清空，避免重复触发。
  prefillText: {
    type: String,
    default: ''
  },
  // 输入草稿的 sessionStorage key：主页面与 AI 侧边栏各存一份
  draftKey: { type: String, default: 'easy_agent_input_draft' },
  // 是否消费技能中心「创建技能」预置的待选技能（只应由主页面输入框消费）
  consumePendingSkill: { type: Boolean, default: true },
  // 「+」二级面板底部的「在管理面板中打开」入口
  showManageEntry: { type: Boolean, default: true },
  // 窄输入框（AI 侧边栏）：工具栏不放专家胶囊（会挤掉发送按钮，已选状态只在「+」→ 专家面板里体现），
  // 专家行改为名称、描述分两行，选中后面板不收起
  compact: { type: Boolean, default: false }
})

const emit = defineEmits(['send', 'stop', 'createSession', 'update:selectedModel', 'typing', 'clearExpert', 'selectExpert', 'prefillApplied', 'openPanel', 'openKnowledge'])

const message = ref('')
const textareaRef = ref(null)
const uploadedFiles = ref([])

// 并行上传时共享的「等待会话创建」Promise（避免重复触发 createSession）
let pendingSessionPromise = null

// ── 上传限额（后端 config.yaml 的 upload_limits 段，经 /agent/settings/upload-limits 下发）──
// 约束「单个会话、单次对话」的附件：即输入框待发送列表，发送后清空即重置额度。
// 拉取失败时保留这里的默认值（10MB / 5 个 / 20MB），绝不因接口异常而放行。
const DEFAULT_UPLOAD_LIMITS = { enabled: true, maxFileSizeMb: 10, maxFiles: 5, maxTotalMb: 20 }
const uploadLimits = ref({ ...DEFAULT_UPLOAD_LIMITS })
// 汇总提示条（单条文案，2.6s 自动消失）
const uploadNotice = ref('')
let uploadNoticeTimer = null

function mbToBytes(mb) {
  return (Number(mb) || 0) * 1024 * 1024
}

// 计额口径：上传失败的文件不占额度（上传中/已上传都占）
const chargedFiles = computed(() => uploadedFiles.value.filter(f => f.uploadStatus !== 'error'))
const usedCount = computed(() => chargedFiles.value.length)
const usedBytes = computed(() => chargedFiles.value.reduce((sum, f) => sum + (f.size || 0), 0))
const failedCount = computed(() => uploadedFiles.value.filter(f => f.uploadStatus === 'error').length)
const remainBytes = computed(() => Math.max(mbToBytes(uploadLimits.value.maxTotalMb) - usedBytes.value, 0))
const limitsText = computed(() => {
  const L = uploadLimits.value
  if (!L.enabled) return ''
  const failedTip = failedCount.value > 0 ? `（${failedCount.value} 个失败未计入）` : ''
  return `已用 ${formatSize(usedBytes.value)} / ${L.maxTotalMb}MB · ${usedCount.value} / ${L.maxFiles} 个${failedTip}`
})
const limitsFull = computed(() => {
  const L = uploadLimits.value
  return L.enabled && usedCount.value >= L.maxFiles
})

function showUploadNotice(text) {
  if (!text) return
  uploadNotice.value = text
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer)
  uploadNoticeTimer = setTimeout(() => {
    uploadNotice.value = ''
  }, 2600)
}

function dismissUploadNotice() {
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer)
  uploadNotice.value = ''
}

async function loadUploadLimits() {
  try {
    const data = await getUploadLimits()
    const toPositive = (v, fallback) => (Number(v) > 0 ? Number(v) : fallback)
    uploadLimits.value = {
      enabled: data.enabled !== false,
      maxFileSizeMb: toPositive(data.max_file_size_mb, DEFAULT_UPLOAD_LIMITS.maxFileSizeMb),
      maxFiles: toPositive(data.max_files_per_message, DEFAULT_UPLOAD_LIMITS.maxFiles),
      maxTotalMb: toPositive(data.max_total_size_mb, DEFAULT_UPLOAD_LIMITS.maxTotalMb),
    }
  } catch (e) {
    console.error('获取上传限额失败，使用默认限额（10MB / 5 个 / 20MB）:', e)
  }
}

/**
 * 选出本批可以上传的文件（入列前校验，避免"传了一半才发现超限"）。
 *
 * 规则：
 * - enabled=false 时不限；
 * - 数量不满足（已有非失败数 + 本批数 > 上限）→ **整批拒绝**，让用户重新选择；
 * - 逐个文件检查单文件大小与总大小 → **只跳过超限的那一个**，其余照常上传；
 * - 失败文件不占额度，因此删除/失败都会立即释放名额与空间。
 */
function pickAcceptable(files) {
  const L = uploadLimits.value
  if (!L.enabled) return { accepted: [...files], rejected: [], batchRejected: false }

  let count = usedCount.value
  let total = usedBytes.value
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
    if (file.size > mbToBytes(L.maxFileSizeMb)) {
      rejected.push({ file, reason: 'file_size' })
      continue
    }
    if (total + file.size > mbToBytes(L.maxTotalMb)) {
      rejected.push({ file, reason: 'total' })
      continue
    }
    accepted.push(file)
    count += 1
    total += file.size
  }
  return { accepted, rejected, batchRejected: false }
}

/** 把被拒文件拼成一条汇总提示 */
function buildRejectNotice(result, acceptedCount) {
  const L = uploadLimits.value
  const parts = result.rejected.map(({ file, reason }) => {
    if (reason === 'file_size') {
      return `${file.name}（超过单文件上限 ${L.maxFileSizeMb}MB）`
    }
    if (reason === 'total') {
      return `${file.name}（超出剩余空间 ${formatSize(remainBytes.value)}，总上限 ${L.maxTotalMb}MB）`
    }
    return file.name
  })
  const head = acceptedCount > 0
    ? `已添加 ${acceptedCount} 个文件，忽略 ${result.rejected.length} 个：`
    : `无法上传，已忽略 ${result.rejected.length} 个文件：`
  return head + parts.join('；')
}

// 外部预填问题（专家详情弹窗的示例提示词）：写入输入框并聚焦，随后通知父组件清空。
// immediate: 必须开启 —— 详情弹窗在 SkillCenter 面板里，点示例问题会关闭面板并让
// Chat/ChatInput 重新挂载，此时 prefillText 初始值即非空；没有 immediate 的 watch
// 只响应"变化"，挂载时的非空初始值会被整个丢掉，回填失效。
watch(
  () => props.prefillText,
  (val) => {
    if (!val) return
    message.value = val
    nextTick(() => {
      autoResize()
      textareaRef.value?.focus()
    })
    emit('prefillApplied')
  },
  { immediate: true }
)

// ========== 技能选择（输入 / 唤起） ==========
// 已选技能不是独立数组，而是**文本里的内联标签**：token 形如 `/显示名`（见 utils/skillPillTokens.js）。
// 标签的可见文字与 textarea 文本逐字相同，所以光标位置、Backspace 判定都是纯文本下标运算，
// 桌面端 / 移动端 / 连续选多个技能的行为完全一致；选中状态由文本推导，不会与文本脱节。
// 只有启用状态的技能会被 Agent 加载，停用技能即使手打 /name 也不生效，故选择面板只列启用的。
const knownSkills = ref([])
const tokenMap = computed(() => buildTokenMap(knownSkills.value))

/** 记住技能元信息（标签 `/显示名` 要靠它反解技能）：/ 面板与「+」面板的接口都会往这里补 */
function rememberSkills(skills) {
  const incoming = (Array.isArray(skills) ? skills : []).filter((s) => s && s.dir_name)
  if (!incoming.length) return
  const byDir = new Map(knownSkills.value.map((s) => [s.dir_name, s]))
  let changed = false
  for (const skill of incoming) {
    const prev = byDir.get(skill.dir_name)
    if (!prev) {
      byDir.set(skill.dir_name, skill)
      changed = true
    } else if ((prev.name || '') !== (skill.name || '')) {
      // 技能改名后，标签要按最新显示名解析
      byDir.set(skill.dir_name, { ...prev, ...skill })
      changed = true
    }
  }
  if (changed) knownSkills.value = Array.from(byDir.values())
}
// 连接器的启用状态改由「+」→ 连接器面板里的开关控制（与「专家·技能·连接器」面板同一份状态），
// 不再点行插入输入框、也不再随每轮消息提交。

// ========== 镜像渲染层（内联技能标签） ==========
// textarea 的文字设为透明，用户看到的文字/标签都来自 .input-mirror 镜像层：
// 标签是文本里的一段子串，这里按 token 把文本分段渲染（文字段 + 标签段），
// 所以标签天然落在它在文本里的位置（第一行、句中、行尾都行）。
// 镜像层必须与 textarea 逐像素同宽同字体，换行位置才会一致 —— 见 .input-mirror 的 CSS 注释。
const mirrorRef = ref(null)
const mirrorText = ref('')
// 上一次 textarea 的文本：移动端兜底自愈要拿它做差分（见 onInput）
let lastInputText = ''

/**
 * 把 textarea 的内容/滚动/滚动条占位同步给镜像层。
 * 输入（含输入法合成中）、程序化赋值、滚动后都要调，否则两侧文字会对不上。
 */
function syncMirror() {
  const ta = textareaRef.value
  const mirror = mirrorRef.value
  if (!ta || !mirror) return
  // 直接读 textarea：输入法合成中的文字 v-model 还没提交，镜像也要实时显示
  mirrorText.value = ta.value
  lastInputText = ta.value
  // 出现纵向滚动条后 textarea 内容区变窄，镜像补同等右内边距，保证两侧换行位置一致
  // （镜像自身还有 MIRROR_BLEED 的 padding 用于扩裁剪盒，要一起算进去，否则内容窄 6px 导致换行错位）
  const gutter = ta.offsetWidth - ta.clientWidth
  mirror.style.paddingRight = gutter > 0 ? `${gutter + MIRROR_BLEED}px` : ''
  // 不加偏移：镜像的 padding-top 与负 margin-top 相互抵消，scrollTop 与 textarea 保持同值即可对齐
  mirror.scrollTop = ta.scrollTop
}


const skillPickerOpen = ref(false)
const skillPickerIndex = ref(0)
const skillPickerLoading = ref(false)
const skillOptions = ref([])
const skillQuery = ref('')
const slashStart = ref(-1)
const inputBoxRef = ref(null)
const skillPickerRef = ref(null)
const skillPickerStyle = ref({})
let skillLoadedAt = 0

// 镜像层的分段结果：普通文字段 + 技能标签段（标签文字与 textarea 文本逐字相同）
const mirrorSegments = computed(() =>
  buildSegments(
    mirrorText.value,
    tokenMap.value,
    // 正在输入 `/查询词` 检索时，半截查询词不要立刻渲染成标签（否则边打字边闪）
    skillPickerOpen.value ? { suppressStart: slashStart.value } : null
  )
)
// 已选技能目录名（按出现顺序去重）：canSend 与发送时的 skills 字段。
// = 输入框内联标签 + 预选技能（preselectedSkillDirs，见 restorePendingSkill）：
// 外部入口（如专家面板「新建/修改专家」）在草稿里用自然语言点名技能，
// 不插内联标签、只把技能记为已选，发送时随 skills 字段交给后端激活。
const preselectedSkillDirs = ref(new Set())

// 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：发送时随请求的
// pending_expert_id 字段上行，后端校验归属后绑定为 save_expert 的修改目标。
const pendingExpertEditId = ref('')

const selectedSkillDirs = computed(() => {
  const dirs = new Set(preselectedSkillDirs.value)
  for (const dir of skillDirsInText(message.value, tokenMap.value)) dirs.add(dir)
  return Array.from(dirs)
})

/**
 * 技能检索：优先按「目录名 / 技能名」匹配；仅当没有任何名称命中时，才回退到描述匹配。
 * 描述是一大段英文说明，几乎每个字母都能命中（如 m → manipulate / from / time），
 * 直接参与匹配会让检索看起来「完全没生效」，因此降级为兜底。
 */
const filteredSkills = computed(() => {
  const q = skillQuery.value.trim().toLowerCase()
  if (!q) return skillOptions.value
  const byName = skillOptions.value.filter(s =>
    (s.dir_name || '').toLowerCase().includes(q) ||
    (s.name || '').toLowerCase().includes(q)
  )
  if (byName.length > 0) return byName
  return skillOptions.value.filter(s => (s.description || '').toLowerCase().includes(q))
})

async function loadSkillOptions() {
  const now = Date.now()
  // 技能列表变动不频繁，60s 内复用上次结果，避免每次输入 / 都打接口
  if (skillOptions.value.length && now - skillLoadedAt < 60000) return
  skillPickerLoading.value = true
  try {
    const data = await getUserSkills()
    // 标签解析要认识所有技能（草稿里可能留着已停用技能的标签），故先登记全量再过滤面板列表
    rememberSkills(data.skills || [])
    skillOptions.value = (data.skills || []).filter(s => s.enabled)
    skillLoadedAt = now
  } catch (e) {
    console.error('获取技能列表失败:', e)
  } finally {
    skillPickerLoading.value = false
  }
}

/** 面板出现在输入框上方（与模型下拉一致的 Teleport + fixed 定位方式） */
function updateSkillPickerPosition() {
  const box = inputBoxRef.value
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
  skillPickerStyle.value = {
    position: 'fixed',
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    maxHeight: `${Math.round(maxHeight)}px`,
    ...(placeAbove
      ? { bottom: `${window.innerHeight - rect.top + margin}px` }
      : { top: `${rect.bottom + margin}px` })
  }
}

/** 键盘切换选项时把当前高亮项滚进可视区（只滚列表本身，不带动外层级） */
function scrollActiveSkillIntoView() {
  const list = skillPickerRef.value?.querySelector('.skill-picker-list')
  const item = list?.children[skillPickerIndex.value]
  if (!list || !item) return
  const top = item.offsetTop
  const bottom = top + item.offsetHeight
  if (top < list.scrollTop) list.scrollTop = top
  else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight
}

/** 判定光标前是否为「/查询词」（/ 位于开头或空白之后），决定展开或收起面板 */
function updateSkillPicker() {
  const ta = textareaRef.value
  if (!ta) return
  const caret = ta.selectionStart ?? ta.value.length
  const slash = parseSlashQuery(ta.value, caret)
  if (!slash) {
    closeSkillPicker()
    return
  }

  slashStart.value = slash.start
  skillQuery.value = slash.query
  skillPickerIndex.value = 0
  skillPickerOpen.value = true
  updateSkillPickerPosition()
  loadSkillOptions()
}

function closeSkillPicker() {
  skillPickerOpen.value = false
  skillPickerIndex.value = 0
  skillQuery.value = ''
  slashStart.value = -1
}

/**
 * 选中技能：删掉刚输入的 /查询词，在光标处插入内联标签 `/显示名`，光标停在标签之后。
 * 已在文本里的同一技能不重复插入（光标落到已有标签之后）。
 */
async function selectSkill(skill) {
  if (!skillPickerOpen.value || !skill || !skill.dir_name) return

  const ta = textareaRef.value
  const base = ta?.value ?? message.value
  const caret = ta?.selectionStart ?? base.length
  // 以当前光标为准重算一次 /查询词 的范围，避免用户中途改动文本后误删内容
  const slash = parseSlashQuery(base, caret)
  const start = slash ? slash.start : caret
  const cleared = base.slice(0, start) + base.slice(caret)

  rememberSkills([skill])
  const existing = skillSegments(cleared, tokenMap.value)
    .find((seg) => seg.dirName === skill.dir_name)
  const next = existing
    ? { text: cleared, caret: existing.end }
    : insertSkillToken(cleared, start, tokenForSkill(skill))

  closeSkillPicker()
  await replaceTextWithCaret(next)
  console.log(`[${new Date().toISOString()}] [技能选择] ${skill.dir_name}`)
}

/**
 * 写入新文本并把光标放回指定下标（标签插入/删除后的统一收尾）。
 * 等 DOM 更新后再设置光标，避免 v-model 回写把光标顶到末尾；焦点始终留在输入框。
 */
function replaceTextWithCaret(next) {
  const ta = textareaRef.value
  message.value = next.text
  return nextTick(() => {
    // v-model 可能在本轮 input 里把旧值又写回 message（输入事件的处理顺序由框架决定），
    // 这里再断言一次，保证 ref 与 DOM 都是我们算好的文本，否则后续发送会带上半个标签。
    if (message.value !== next.text) message.value = next.text
    autoResize()
    if (ta) {
      if (ta.value !== next.text) ta.value = next.text
      ta.focus()
      ta.setSelectionRange(next.caret, next.caret)
    }
    updateCaretLine()
    syncMirror()
  })
}

/** 整段删除技能标签 + 复位光标（Backspace / Delete / 点击标签都走这里） */
function applyTokenRemoval(base, segsToRemove) {
  if (!Array.isArray(segsToRemove) || !segsToRemove.length) return
  replaceTextWithCaret(removeTokenSegments(base, segsToRemove))
}

/** 点击标签 / 面板里取消选择：把该技能的标签从文本里整段删掉 */
function removeSkillToken(dirName) {
  const ta = textareaRef.value
  const base = ta?.value ?? message.value
  const segs = skillSegments(base, tokenMap.value).filter((seg) => seg.dirName === dirName)
  if (!segs.length) return
  applyTokenRemoval(base, segs)
}

/**
 * 恢复「待选技能」：技能中心「创建技能」等入口会把技能写进 sessionStorage，
 * 面板关闭后本组件重新挂载时读出来挂成技能标签（读后即清除，避免下次误挂）。
 *
 * `insert_token === false` 时**不插标签**：草稿正文已用自然语言点名技能
 * （如专家面板的「用「expert-creator」技能帮我创建：…」），这里只把技能
 * 记为预选，发送时随 skills 字段交给后端激活。
 */
function restorePendingSkill() {
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
    const list = Array.isArray(parsed?.skills) ? parsed.skills : (parsed ? [parsed] : [])
    for (const skill of list) {
      if (!skill?.dir_name) continue
      rememberSkills([skill])
      if (skill.insert_token === false) {
        preselectedSkillDirs.value.add(skill.dir_name)
        continue
      }
      const ta = textareaRef.value
      const base = ta?.value ?? message.value
      if (skillSegments(base, tokenMap.value).some((seg) => seg.dirName === skill.dir_name)) continue
      replaceTextWithCaret(insertSkillToken(base, base.length, tokenForSkill(skill)))
    }
  } catch (e) {
    console.error('恢复待选技能失败:', e)
  }
}

/**
 * Backspace / Delete 紧邻标签时整段删除标签（光标在别处则保持常规的逐字符删除）。
 * 光标「紧邻标签之后」= selectionStart === 标签结束下标，与文本是同一套下标，桌面端/移动端一致。
 */
function handleTokenDeleteKey(event) {
  if (event.isComposing) return false
  if (event.key !== 'Backspace' && event.key !== 'Delete') return false
  const ta = event.target
  if (!ta || typeof ta.selectionStart !== 'number') return false
  // 有选区时按常规删除（用户是在删一段选中的文本）
  if (ta.selectionStart !== ta.selectionEnd) return false
  const caret = ta.selectionStart
  const seg = event.key === 'Backspace'
    ? tokenBeforeCaret(ta.value, caret, tokenMap.value)
    : tokenAfterCaret(ta.value, caret, tokenMap.value)
  if (!seg) return false
  event.preventDefault()
  applyTokenRemoval(ta.value, [seg])
  return true
}

/**
 * beforeinput 路径：移动端（Android Chrome）Backspace 不给 keydown，键盘删除只能在这里拦。
 * 拦截后自行整段删标签；拦不到的情况由 onInput 的差分自愈兜底（见 reconcileTokenDeletion）。
 */
function onBeforeInput(event) {
  if (event.isComposing) return
  const deleteBackward = event.inputType === 'deleteContentBackward'
  const deleteForward = event.inputType === 'deleteContentForward'
  if (!deleteBackward && !deleteForward) return
  const ta = event.target
  if (!ta || typeof ta.selectionStart !== 'number') return
  if (ta.selectionStart !== ta.selectionEnd) return
  const caret = ta.selectionStart
  const seg = deleteBackward
    ? tokenBeforeCaret(ta.value, caret, tokenMap.value)
    : tokenAfterCaret(ta.value, caret, tokenMap.value)
  if (!seg) return
  event.preventDefault()
  applyTokenRemoval(ta.value, [seg])
}

/** 键盘：面板打开时接管 ↑↓ / Enter / Esc，其余情况保持原有 Enter 发送 */
function onKeydown(event) {
  if (handleTokenDeleteKey(event)) return

  if (skillPickerOpen.value) {
    const count = filteredSkills.value.length
    if (event.key === 'ArrowDown' && count > 0) {
      event.preventDefault()
      skillPickerIndex.value = (skillPickerIndex.value + 1) % count
      nextTick(scrollActiveSkillIntoView)
      return
    }
    if (event.key === 'ArrowUp' && count > 0) {
      event.preventDefault()
      skillPickerIndex.value = (skillPickerIndex.value - 1 + count) % count
      nextTick(scrollActiveSkillIntoView)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      closeSkillPicker()
      return
    }
    if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
      const skill = filteredSkills.value[skillPickerIndex.value]
      if (skill) {
        event.preventDefault()
        selectSkill(skill)
        return
      }
      closeSkillPicker()
    }
  }

  if (
    event.key === 'Enter' &&
    !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey &&
    !event.isComposing
  ) {
    event.preventDefault()
    send()
  }
}

/** 点击面板外的区域时收起技能面板 */
function closeSkillPickerOnClick(event) {
  if (!skillPickerOpen.value) return
  const picker = skillPickerRef.value
  if (picker && picker.contains(event.target)) return
  closeSkillPicker()
}

/** 窗口尺寸变化时重新定位（移动端键盘弹出会触发 resize） */
function handleViewportChange() {
  if (skillPickerOpen.value) updateSkillPickerPosition()
}

// ========== 联网搜索 ==========
// 开关状态仅本次页面会话有效（默认关闭）：开启后本轮消息携带 enable_web_search，
// 后端注入 web_search 工具并追加联网搜索行为约束。
const knowledgeEnabled = ref(false)
const webSearchEnabled = ref(false)
const webSearchStatus = ref({ enabled: false, configured: false })
const webSearchUnavailable = computed(() => !webSearchStatus.value.configured)
const webSearchTitle = computed(() => {
  if (!webSearchStatus.value.configured) {
    return '联网搜索未配置（需配置 WEB_SEARCH_API_KEY 后重启后端）'
  }
  return webSearchEnabled.value ? '联网搜索已开启，点击关闭' : '联网搜索（点击开启）'
})

function toggleWebSearch() {
  if (webSearchUnavailable.value || props.isStreaming || props.disabled) return
  webSearchEnabled.value = !webSearchEnabled.value
  console.log(
    `[${new Date().toISOString()}] [联网搜索] ${webSearchEnabled.value ? '已开启' : '已关闭'}`
  )
}

async function loadKnowledgeStatus() {
  try {
    const data = await getKnowledgeStatus()
    knowledgeEnabled.value = Boolean(data && data.enabled)
  } catch (e) {
    knowledgeEnabled.value = false
  }
}

async function loadWebSearchStatus() {
  try {
    const data = await getWebSearchStatus()
    webSearchStatus.value = {
      enabled: !!data.enabled,
      configured: !!data.configured,
    }
    if (!data.configured) webSearchEnabled.value = false
  } catch (e) {
    // 状态获取失败按「不可用」处理，避免用户开启后静默不生效
    console.error('获取联网搜索状态失败:', e)
  }
}

// 待选技能：由技能中心「创建技能」等入口写入，本组件挂载时恢复成技能标签
const PENDING_SKILL_KEY = 'easy_agent_pending_skill'

// 待修改专家：由专家面板「编辑」写入（{expert_agent_id, export_name_cn}）。
// 专家 ID 不进消息正文（防篡改），挂载时消费为组件状态，发送时随请求的
// pending_expert_id 字段上行；发送后清空。与后端 save_expert 的 edit_targets 绑定配套。
const PENDING_EXPERT_EDIT_KEY = 'easy_agent_pending_expert_edit'

// 光标所在行高亮（overlay 技术：textareal 本身无法按行上色）
const caretLineTop = ref(null)   // 高亮条 top，null 表示隐藏
const lineHeight = ref(24)       // 行高（px）

function updateCaretLine() {
  syncMirror() // 聚焦/点击/按键/滚动都会走到这里，顺带把镜像层对齐（含 scrollTop）
  const ta = textareaRef.value
  if (!ta) return
  const lh = parseFloat(getComputedStyle(ta).lineHeight)
  if (!isNaN(lh) && lh > 0) lineHeight.value = lh
  const pos = ta.selectionStart ?? 0
  const lineIndex = ta.value.slice(0, pos).split('\n').length - 1
  caretLineTop.value = lineIndex * lineHeight.value - ta.scrollTop
}

function hideCaretLine() {
  caretLineTop.value = null
}

// 模型切换：下拉交互在 ModelPicker 组件内，这里只负责向上抛事件
function onModelChange(name) {
  emit('update:selectedModel', name)
  console.log(
    `[${new Date().toISOString()}] [模型选择] 切换为: ${name}`
  )
}

// ========== 「+」添加菜单 ==========
const fileInputRef = ref(null)
const showPlusMenu = ref(false)
const plusMenuRef = ref(null)
// 菜单本身（Teleport 到 body）：默认展开连接器子面板时要用它找「连接器」菜单项定位
const plusMenuListRef = ref(null)
const plusMenuStyle = ref({})
const knowledgeScope = ref(null)

function togglePlusMenu() {
  if (props.isStreaming || props.disabled) return
  if (showPlusMenu.value) {
    showPlusMenu.value = false
    subpanelCategory.value = null
    knowledgeScope.value?.closePopover()
    cancelPlusClose()
    return
  }
  // 菜单贴着按钮上方弹出（与模型下拉同款定位方式）
  if (plusMenuRef.value) {
    const rect = plusMenuRef.value.getBoundingClientRect()
    plusMenuStyle.value = {
      position: 'fixed',
      bottom: `${window.innerHeight - rect.top + 6}px`,
      left: `${rect.left}px`,
      minWidth: '188px',
    }
  }
  subpanelCategory.value = null
  showPlusMenu.value = true
  // 「+」= 展开连接器面板的触发器：菜单弹出后直接把连接器子面板展开在「连接器」
  // 菜单项右侧（面板定位依赖该菜单项元素，要等 Teleport 渲染完成）。
  nextTick(openConnectorsSubpanelFromMenu)
}

/** 「+」菜单打开时默认展开连接器子面板（定位参照菜单里的「连接器」项） */
function openConnectorsSubpanelFromMenu() {
  const anchor = plusMenuListRef.value?.querySelector('[data-category="connectors"]')
  if (!anchor) return
  openSubpanel('connectors', { currentTarget: anchor }, true)
}

function closePlusMenu(event) {
  // 点击知识库面板内部不算「菜单外」（面板不在菜单 DOM 内，但属于同一浮层交互）
  const scopeRoot = knowledgeScope.value?.$el
  const inScope = scopeRoot && scopeRoot.contains(event.target)
  if (showPlusMenu.value && plusMenuRef.value && !plusMenuRef.value.contains(event.target) && !inScope) {
    showPlusMenu.value = false
    subpanelCategory.value = null
    knowledgeScope.value?.closePopover()
  }
  // 技能面板内部点击已被 @click.stop 拦截，能到这里说明点在面板外
  if (subpanelCategory.value && plusMenuRef.value && !plusMenuRef.value.contains(event.target)) {
    subpanelCategory.value = null
  }
}

function handleMenuAddFile() {
  showPlusMenu.value = false
  subpanelCategory.value = null
  knowledgeScope.value?.closePopover()
  fileInputRef.value?.click()
}

// 加号菜单「知识库」：点击保持主菜单常驻，面板贴菜单项右侧弹出
function openKnowledgeScope(event) {
  cancelPlusClose()
  subpanelCategory.value = null
  knowledgeScope.value?.openPopover(event?.currentTarget)
}

// 知识库子面板底部入口：关闭菜单并打开知识库工作台
function openKnowledgeManage() {
  showPlusMenu.value = false
  subpanelCategory.value = null
  knowledgeScope.value?.closePopover()
  emit('openKnowledge')
}

// ========== 「+」二级列表：展示当前用户自己的条目（不跳转页面） ==========
// 数据来源均为当前用户：
//   技能   → GET /agent/skill-center/user-skills（workspace/{user}/skills，含已停用）
//   连接器 → GET /agent/connectors（用户 mcp.json 中已安装的内置连接器 + 自定义服务）
//   专家   → 我的专家（/agent/list，仅取可用状态） + 公共专家（/agent/public-list）
// 「+」子面板尺寸与贴边间距：JS 定位与 CSS 宽度必须同源，否则放大后会溢出视口。
// 单行布局（名称 + 描述同排）需要更宽的面板，400px 下描述只剩 130px 左右不够看。
const SUBPANEL_WIDTH = 480
// 子面板与主菜单之间的水平间隙；CSS 里的 ::before 热桥按「间隙 + 1px 重叠」覆盖这段空白
const SUBPANEL_GAP = 6
// 子面板高度上限。面板整体底边贴主菜单底边、向上生长，可视空间有多大就长多高，
// 但封顶在这个值——否则技能多的用户会顶到屏幕顶端，观感像「整个页面被面板占住」。
// 按行高约 44px 算，560px ≈ 列表能露出 10 行，比原来的 320px 一屏 5 行舒服很多。
const SUBPANEL_MAX_HEIGHT = 560

const subpanelCategory = ref(null) // 当前展开的二级面板：'skills' | 'experts' | 'connectors' | null
const plusListCategory = ref('')
const plusList = ref([])
const plusListLoading = ref(false)
const plusListError = ref('')
// 专家快捷面板：上区我的专家 / 下区公共（预置）专家
const myExperts = ref([])
const publicExperts = ref([])
// 两个区块默认各展示 6 位（单行布局后一屏放得下 6 行），滚动到底部再放出 3 位
const plusExpertsLimit = ref(6)
// 子面板搜索（专家 / 技能 / 连接器共用一个关键词，切换分类时清空）
const subpanelKeyword = ref('')

const PLUS_LIST_META = {
  experts: { title: '专家', empty: '暂无专家，敬请期待' },
  skills: { title: '我的技能', empty: '还没有自己的技能，可在「专家·技能·连接器」面板添加' },
  connectors: { title: '我的连接器', empty: '还没有已添加的连接器，可在「专家·技能·连接器」面板添加' },
}

const plusListTitle = computed(() => PLUS_LIST_META[plusListCategory.value]?.title || '')
const plusListEmptyText = computed(() => PLUS_LIST_META[plusListCategory.value]?.empty || '暂无内容')
const plusListSearchPlaceholder = computed(() => {
  const meta = { experts: '搜索专家', skills: '搜索技能', connectors: '搜索连接器' }
  return meta[plusListCategory.value] || '搜索'
})

// 我的专家：仅保留「可用」（active）状态，与专家 Tab 的召唤约束一致
const myExpertsActive = computed(() =>
  myExperts.value.filter((item) => item && item.agent_status === 'active')
)

/**
 * 专家搜索：名称 / 描述 / 依赖技能名。
 * 带上技能名是因为用户常按「这个专家用了什么技能」来找，而描述里未必出现该词。
 */
function filterExperts(list) {
  const kw = subpanelKeyword.value.trim().toLowerCase()
  if (!kw) return list || []
  return (list || []).filter((item) => {
    if (!item) return false
    const name = String(item.export_name_cn || '').toLowerCase()
    const desc = String(item.export_description || '').toLowerCase()
    const skills = Array.isArray(item.export_skills_names)
      ? item.export_skills_names.join(' ').toLowerCase()
      : String(item.export_skills_names || '').toLowerCase()
    return name.includes(kw) || desc.includes(kw) || skills.includes(kw)
  })
}

const filteredMyExperts = computed(() => filterExperts(myExpertsActive.value))
const filteredPublicExperts = computed(() => filterExperts(publicExperts.value))
// 有搜索词时，清空的区连标题一起收起（避免「标题 + 空白」的空壳）
const showMineSection = computed(() => !subpanelKeyword.value.trim() || filteredMyExperts.value.length > 0)
const showPublicSection = computed(() => !subpanelKeyword.value.trim() || filteredPublicExperts.value.length > 0)

// 搜索时全量展示（避免「搜到第 4 条却藏在『还有 N 位』后面」），否则按 limit 分页
const visibleMyExperts = computed(() =>
  subpanelKeyword.value.trim()
    ? filteredMyExperts.value
    : filteredMyExperts.value.slice(0, plusExpertsLimit.value)
)
const visiblePublicExperts = computed(() =>
  subpanelKeyword.value.trim()
    ? filteredPublicExperts.value
    : filteredPublicExperts.value.slice(0, plusExpertsLimit.value)
)

// 标题右侧计数：过滤后的条数（专家 = 两区之和，技能 = 技能列表过滤后条数）
const plusListCount = computed(() => {
  if (plusListCategory.value === 'experts') {
    return filteredMyExperts.value.length + filteredPublicExperts.value.length
  }
  if (plusListCategory.value === 'skills') return filteredSkillList.value.length
  return filteredPlusList.value.length
})

// 空列表时不显示搜索框（免得对着空列表以为「搜不到」）
const hasPanelItems = computed(() =>
  plusListCategory.value === 'experts'
    ? myExpertsActive.value.length + publicExperts.value.length > 0
    : plusList.value.length > 0
)

function openPlusList(category) {
  plusListCategory.value = category
  plusListLoading.value = true
  plusListError.value = ''
  plusList.value = []
  // 每次打开都重新拉取（接口很轻，且公共专家目录是服务端实时扫描的）
  myExperts.value = []
  publicExperts.value = []
  plusExpertsLimit.value = 6
  // 三个面板共用一份搜索词：切分类时一律清空，避免把上一个面板的关键字带过来
  subpanelKeyword.value = ''
  if (category === 'connectors') {
    // 开关状态随连接器目录一起拉取（loadPlusList）
    clearConnectorNotice()
  }
  loadPlusList(category)
}

// ========== 悬停触发二级面板 ==========
// 鼠标悬停主菜单项即切换对应面板（技能=独立子面板，专家/连接器=list 模式），
// 无需点击；点击仍保留（触屏/键盘回退）。鼠标离开「主菜单 + 子面板」整个区域
// 超过 250ms 才整体关闭，留出移入子面板的时间（两者之间有空隙）。
let plusHoverTimer = null

function cancelPlusClose() {
  if (plusHoverTimer) {
    clearTimeout(plusHoverTimer)
    plusHoverTimer = null
  }
}

function schedulePlusClose() {
  cancelPlusClose()
  // 保护一：焦点在面板内（搜索框有光标）。面板会随搜索结果变矮，鼠标可能被
  // 布局变化「抽」到面板外触发 mouseleave —— 此刻不能关，否则就是
  // 「搜索一下就消失」。失焦后再判断（见 onSubpanelFocusOut）。
  if (subpanelFocused.value) return
  // 保护二：鼠标还停在面板上
  if (subpanelHovered.value) return
  // 350ms：给「从菜单项斜移进子面板」留足时间（250ms 在面板较远时不够用）。
  // 真正的兜底是 .skill-submenu::before 的热桥，这里只是余量。
  plusHoverTimer = setTimeout(() => {
    plusHoverTimer = null
    subpanelHovered.value = false
    subpanelFocused.value = false
    showPlusMenu.value = false
    subpanelCategory.value = null
    knowledgeScope.value?.closePopover()
  }, 350)
}

/** 进入面板：取消关闭计时（面板会变矮，鼠标可能中途「悬空」，靠这个标志兜住） */
function onSubpanelEnter() {
  subpanelHovered.value = true
  cancelPlusClose()
}

function onSubpanelLeave() {
  subpanelHovered.value = false
  schedulePlusClose()
}

/** 焦点进入面板（搜索框 / 行按钮）：锁定，不因鼠标移出而关闭 */
function onSubpanelFocusIn() {
  subpanelFocused.value = true
  cancelPlusClose()
}

/** 焦点离开面板：只有在面板内部换焦点才算没走（focusout 早于新的 focusin） */
function onSubpanelFocusOut(e) {
  if (e && e.relatedTarget && e.currentTarget && e.currentTarget.contains(e.relatedTarget)) return
  subpanelFocused.value = false
  if (!subpanelHovered.value) schedulePlusClose()
}

function hoverPlusItem(category, event) {
  cancelPlusClose()
  // 知识库与技能/专家一致：悬停即展开面板，无需点击
  if (category === 'knowledge') {
    subpanelCategory.value = null
    const anchor = plusMenuListRef.value?.querySelector('.plus-menu-item-knowledge') || event?.currentTarget
    knowledgeScope.value?.openPopover(anchor)
    return
  }
  // 切到其他菜单项时收起知识库面板，避免两个浮层叠着
  knowledgeScope.value?.closePopover()
  if (category === 'file') {
    // 无子面板的项：收起子面板，主菜单保持
    subpanelCategory.value = null
    return
  }
  openSubpanel(category, event, true)
}

async function loadPlusList(category) {
  try {
    if (category === 'experts') {
      // 并行拉取：我的专家（数据库） + 公共专家（内置目录）
      const [mine, preset] = await Promise.all([
        listExperts({ includeDisabled: false }),
        listPublicExperts(),
      ])
      myExperts.value = mine.items || []
      publicExperts.value = preset.items || []
    } else if (category === 'skills') {
      const data = await getUserSkills()
      plusList.value = (data.skills || []).map((s) => ({
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
      plusList.value = [...installed, ...custom]
    } else {
      plusList.value = []
    }
  } catch (e) {
    plusListError.value = e.message || '加载失败'
  } finally {
    plusListLoading.value = false
    // 首屏数据到位后才是真实高度：重算一次（高度锁只增，所以这里只会把占位值矫正掉）
    nextTick(syncSubpanelBox)
  }
}

// ========== 专家快捷面板：行渲染与选择 ==========
// 头像取字与配色统一走 utils/expertAvatar.js（全局 8 色板 .ea-0 ~ .ea-7）

/** 行右侧的浅灰小字描述：优先功能描述，回退专家描述 */
function expertDesc(item) {
  return (
    String(item?.export_description || '').replace(/\s*\n+\s*/g, ' ').trim() ||
    String(item?.export_prompt || '').replace(/\s*\n+\s*/g, ' ').trim() ||
    '暂无描述'
  )
}

/** 是否为当前会话已召唤的专家（activeExpert 由 App.vue 传入） */
function isExpertSelected(item) {
  if (!item || !props.activeExpert) return false
  return props.activeExpert.export_agent_id === item.export_agent_id
}

/**
 * 点击专家行：与专家 Tab 的「召唤」同一链路。
 * 已选中的再点一次 = 取消召唤（同时清掉输入框右下角的专家胶囊）。
 * 操作完成后直接关闭面板（选中态由胶囊与「已选」标承载）。
 */
function toggleExpert(item) {
  if (!item || !item.export_agent_id) return
  if (isExpertSelected(item)) {
    emit('clearExpert')
  } else {
    emit('selectExpert', item)
  }
  if (props.compact) return
  showPlusMenu.value = false
  subpanelCategory.value = null
  knowledgeScope.value?.closePopover()
  cancelPlusClose()
}

/** 列表滚动到底部附近时，每区多放出 3 条（防止面板一次性过高） */
function onExpertsScroll(e) {
  const el = e?.target
  if (!el) return
  const reachedBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8
  if (!reachedBottom) return
  loadMoreExperts()
}

/** 每区多放出 3 条（滚动到底 / 点「还有 N 位」都会走到这里） */
function loadMoreExperts() {
  const total = Math.max(myExpertsActive.value.length, publicExperts.value.length)
  if (plusExpertsLimit.value < total) {
    plusExpertsLimit.value += 3
    // 内容变多是唯一允许面板长高的场景（向上长），重算一次尺寸
    nextTick(syncSubpanelBox)
  }
}

// 列表底部入口：跳到「专家·技能·连接器」面板做完整管理（启用/停用、编辑等）
function handleMenuPanel(tab) {
  showPlusMenu.value = false
  subpanelCategory.value = null
  emit('openPanel', tab)
}

// ========== 技能选择弹出面板 ==========
// 从「+」菜单的「技能」项右侧弹出（样式对齐参考稿）：顶部搜索、技能列表、
// 底部「从本地添加技能 / 管理技能」。点击技能项把它以令牌形式插入输入框。
const skillSubmenuRef = ref(null)
const skillSubmenuStyle = ref({})
const skillSearch = ref('')
const skillList = ref([])
const skillListLoading = ref(false)
const skillListError = ref('')
const skillImportInputRef = ref(null)
// 子面板 DOM（技能/专家/连接器三者互斥，共用同一个 ref）：拿真实高度算尺寸，见 syncSubpanelBox。
const subpanelEl = ref(null)
// 一级菜单（.plus-menu）的视口坐标（顶边 / 底边）。
// 子面板的底边活动范围就是 [plusMenuTop, plusMenuBottom]：打开时贴底边（左右两面板
// 底部齐平），内容变少时底边往上收，最多收到与顶边齐平（面板整个落到菜单上方）。
// 只在 openSubpanel 里写一次，之后 syncSubpanelBox 反复读。
const plusMenuTop = ref(0)
const plusMenuBottom = ref(0)
// 本次打开以来量到过的「最满」高度，作为顶边基准（见 syncSubpanelBox）。
const subpanelRefHeight = ref(0)
// 关闭保护：鼠标是否停在面板上、焦点是否在面板内（搜索框）。
// 面板会随搜索结果变矮，鼠标可能被「抽走」到面板外 —— 聚焦期间一律不自动关闭，
// 等失焦且鼠标也不在面板上时才关（见 schedulePlusClose）。
const subpanelHovered = ref(false)
const subpanelFocused = ref(false)

// 图标方块配色（参考稿中的彩色字母方块），按列表顺序循环取色
const SKILL_ICON_COLORS = [
  { bg: '#fde68a', fg: '#92400e' },
  { bg: '#bbf7d0', fg: '#166534' },
  { bg: '#c7d2fe', fg: '#3730a3' },
  { bg: '#fbcfe8', fg: '#9d174d' },
  { bg: '#bae6fd', fg: '#075985' },
  { bg: '#ddd6fe', fg: '#5b21b6' },
]

const filteredSkillList = computed(() => {
  const kw = skillSearch.value.trim().toLowerCase()
  if (!kw) return skillList.value
  // 与 / 技能选择面板同一套规则：先按技能名/目录名匹配，描述（大段英文说明）只作兜底，
  // 否则输入单个字母（如 m）会把整张列表都匹配出来，看起来像「搜索没生效」。
  const byName = skillList.value.filter((s) =>
    s.name.toLowerCase().includes(kw) || (s.dir_name || '').toLowerCase().includes(kw)
  )
  if (byName.length > 0) return byName
  return skillList.value.filter((s) => s.desc.toLowerCase().includes(kw))
})

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
 * 为什么不用 `bottom: 固定值`：底边钉住时内容变矮会让顶边（含搜索框）往下移，
 * 鼠标原地掉出面板 → mouseleave → 面板关闭。现在允许变矮，靠「搜索框聚焦期间
 * 不自动关闭」兜住（见 schedulePlusClose）。
 *
 * 也不能退回「按假设高度 420px 判断上下翻转」的老办法：这里用真实高度算。
 */
function openSubpanel(category, event, fromHover = false) {
  // hover 触发时同面板已打开则保持（避免鼠标在项与面板间移动导致闪烁）；
  // click 触发保持翻转语义（再次点击同一项关闭）
  if (subpanelCategory.value === category) {
    if (fromHover) return
    subpanelCategory.value = null
    return
  }
  subpanelCategory.value = category
  // 换面板时先清掉可能残留的关闭计时器（例如从搜索框失焦后立刻点了另一个菜单项）
  cancelPlusClose()
  subpanelFocused.value = false
  subpanelHovered.value = false
  subpanelRefHeight.value = 0
  const anchor = event.currentTarget
  const rect = anchor.getBoundingClientRect()
  // 对齐基准是一级菜单容器（.plus-menu）：底边决定「齐平」，顶边是上收的极限
  const menuEl = anchor.closest ? anchor.closest('.plus-menu') : null
  const menuRect = menuEl ? menuEl.getBoundingClientRect() : null
  plusMenuTop.value = menuRect ? menuRect.top : rect.top
  plusMenuBottom.value = menuRect ? menuRect.bottom : rect.bottom
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
    Math.min(SUBPANEL_MAX_HEIGHT, plusMenuBottom.value - Math.max(8, plusMenuTop.value))
  )
  skillSubmenuStyle.value = {
    position: 'fixed',
    left: `${left}px`,
    width: `${width}px`,
    top: `${Math.max(8, plusMenuBottom.value - provisionalMax)}px`,
    height: 'auto',
    maxHeight: `${provisionalMax}px`,
  }
  // 三个面板共用一套标题/占位/计数文案（PLUS_LIST_META），技能面板也要走到这里，
  // 否则标题行与搜索占位会是空的（技能列表走 loadSkillSubList，不经 openPlusList）。
  plusListCategory.value = category
  // 三个面板共用一份搜索词：切分类时一律清空，避免把上一个面板的关键字带过来
  subpanelKeyword.value = ''
  if (category === 'skills') {
    skillSearch.value = ''
    loadSkillSubList()
  } else {
    openPlusList(category)
  }
  nextTick(syncSubpanelBox)
}

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
function syncSubpanelBox() {
  const el = subpanelEl.value
  const dom = el && el.$el ? el.$el : el
  if (!dom) return
  const viewportH = window.innerHeight
  // 拿不到基准时退化到视口下半部，保证仍能算出一个合理位置
  const menuTop = plusMenuTop.value || Math.round(viewportH * 0.6)
  const menuBottom = plusMenuBottom.value || menuTop + 40
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
  if (height > subpanelRefHeight.value) subpanelRefHeight.value = height
  const refHeight = subpanelRefHeight.value || height
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
  skillSubmenuStyle.value = {
    ...skillSubmenuStyle.value,
    top: `${top}px`,
    height: 'auto',
    maxHeight: `${refHeight}px`,
  }
}

async function loadSkillSubList() {
  skillListLoading.value = true
  skillListError.value = ''
  try {
    const data = await getUserSkills()
    rememberSkills(data.skills || [])
    skillList.value = (data.skills || []).map((s, i) => {
      const name = s.name || s.dir_name
      const color = SKILL_ICON_COLORS[i % SKILL_ICON_COLORS.length]
      return {
        dir_name: s.dir_name,
        name,
        desc: s.description || '暂无描述',
        icon: s.icon || '',
        category_id: s.category_id,
        enabled: !!s.enabled,
        initial: name.slice(0, 1).toUpperCase(),
        iconBg: color.bg,
        iconFg: color.fg,
      }
    })
  } catch (e) {
    skillListError.value = e.message || '加载技能失败'
  } finally {
    skillListLoading.value = false
    // 首屏数据到位后才是真实高度：重算一次（高度锁只增，所以这里只会把占位值矫正掉）
    nextTick(syncSubpanelBox)
  }
}

// ========== 技能标签：插入输入框文本，标识本会话启用该技能 ==========
// 「/ 唤起」与「+」→ 技能面板共用同一套文本标签（见 utils/skillPillTokens.js）：
// 标签落在光标处，选中后光标停在标签之后，删除整段进行。
function isSkillSelected(dirName) {
  return selectedSkillDirs.value.includes(dirName)
}

/**
 * 「+」→ 技能子面板点行：挂载 / 取消技能标签，行为与「/」唤起技能完全一致。
 * 停用技能不参与：Agent 只加载已启用的技能，挂上去也不会生效。
 */
function onSkillSubItemClick(item) {
  if (!item || !item.dir_name || !item.enabled) return
  toggleSkillToken(item)
}

function toggleSkillToken(item) {
  if (isSkillSelected(item.dir_name)) {
    removeSkillToken(item.dir_name)
    return
  }
  rememberSkills([{ ...item, description: item.description || item.desc }])
  const ta = textareaRef.value
  const base = ta?.value ?? message.value
  const caret = typeof ta?.selectionStart === 'number' ? ta.selectionStart : base.length
  replaceTextWithCaret(insertSkillToken(base, caret, tokenForSkill(item)))
  console.log(`[${new Date().toISOString()}] [技能选择] ${item.dir_name}`)
}

// ========== 连接器开关（「+」→ 连接器，替代旧的「点行插入输入框胶囊」） ==========
// 开关直接对应连接器自身的启用状态（用户 mcp.json 里每个 server 的 enabled），
// 与「专家·技能·连接器」面板是同一份状态：这里改，那边实时跟着变（见 connectorSync.js）。
// 正在提交的连接器 id（该行开关暂时禁用，避免连点产生竞态）。
const connectorToggling = ref('')
// 操作反馈：改完开关在面板内提示一句，3s 后自动消失
const connectorNotice = ref('')
const connectorNoticeType = ref('success')
let connectorNoticeTimer = null
// 连接器状态变更事件的取消订阅函数（onMounted 注册、onUnmounted 解绑）
let offConnectorsChanged = null
// 面板内的搜索词已统一为 subpanelKeyword（见上方「+」二级列表区域）

/** 连接器行在会话开关里的标识（目录条目用 id，自定义服务用 server 名） */
function connectorKeyOf(item) {
  return (item && item.connectorId) || ''
}

/**
 * 【技能 / 连接器】面板的搜索结果。
 * 名称（含技能目录名）优先命中；描述是一大段说明，几乎每个词都能命中，
 * 因此只在名称完全没命中时才回退到描述（与「/」检索同一取舍）。
 */
const filteredPlusList = computed(() => {
  const kw = subpanelKeyword.value.trim().toLowerCase()
  if (!kw) return plusList.value
  const byName = plusList.value.filter((item) => {
    const name = String(item.name || '').toLowerCase()
    const dir = String(item.dir_name || '').toLowerCase()
    return name.includes(kw) || dir.includes(kw)
  })
  if (byName.length > 0) return byName
  return plusList.value.filter((item) => String(item.desc || '').toLowerCase().includes(kw))
})

/**
 * 连接器在 mcp.json 里是否启用。
 * 「专家·技能·连接器」面板的开关读的是同一个字段：两处必须显示同一状态。
 */
function connectorEnabled(item) {
  return !!item && item.enabled !== false
}

/** 该连接器的开关是否为「打开」 */
function isConnectorOn(item) {
  return !!connectorKeyOf(item) && connectorEnabled(item)
}

/**
 * 开关是否不可点。
 * 启用与停用都允许在本面板直接切换（与「专家·技能·连接器」面板同一份状态），
 * 只有「正在提交该行」时才临时禁用，避免连点产生竞态。
 */
function isConnectorSwitchDisabled(item) {
  const key = connectorKeyOf(item)
  if (!key) return true
  return connectorToggling.value === key
}

/** 该行开关是否正在提交（显示忙碌态的转圈） */
function isConnectorBusy(item) {
  const key = connectorKeyOf(item)
  return !!key && connectorToggling.value === key
}

/** 开关的无障碍名称 */
function connectorSwitchLabel(item) {
  if (isConnectorBusy(item)) return `正在保存连接器 ${item.name} 的开关`
  return `${isConnectorOn(item) ? '停用' : '启用'}连接器 ${item.name}`
}

/** 开关的 title：说清点下去会发生什么、以及会同步到哪里 */
function connectorSwitchTitle(item) {
  if (isConnectorBusy(item)) return '正在保存…'
  return isConnectorOn(item)
    ? '已启用（点击停用，「专家·技能·连接器」面板同步生效）'
    : '已停用（点击启用，「专家·技能·连接器」面板同步生效）'
}

/** 面板内的操作反馈（连接器开关），ms 后自动消失 */
function showConnectorNotice(message, type = 'success', ms = 2600) {
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer)
  connectorNotice.value = message
  connectorNoticeType.value = type
  connectorNoticeTimer = setTimeout(() => {
    connectorNoticeTimer = null
    connectorNotice.value = ''
  }, ms)
}

/** 清掉提示（打开面板时调用，避免看到上一次操作留下的文案） */
function clearConnectorNotice() {
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer)
  connectorNoticeTimer = null
  connectorNotice.value = ''
}

/** 按最新状态刷新某个连接器行的开关与 tag（tag 停用时统一显示「已停用」） */
function applyConnectorState(id, enabled) {
  if (!id) return
  const item = plusList.value.find((i) => connectorKeyOf(i) === id)
  if (!item) return
  item.enabled = !!enabled
  item.tag = item.enabled ? (item.enabledTag || '') : '已停用'
  item.tagType = item.enabled ? (item.enabledTagType || 'on') : 'off'
}

/** 其它面板（「专家·技能·连接器」）改了开关：同步本面板显示（忽略自己派发的回声） */
function onConnectorsChangedHandler(payload) {
  if (!payload || payload.source === 'plus-panel') return
  applyConnectorState(payload.id, payload.enabled)
}

/**
 * 切换连接器的启用状态：直接改连接器自身的开关（后端写用户 mcp.json 的 enabled，
 * 全局生效），并广播给「专家·技能·连接器」面板，保证两处状态一致。
 * 启用 → 停用、停用 → 启用都走这里，两个面板都可以操作。
 */
async function toggleConnectorSwitch(item) {
  const id = connectorKeyOf(item)
  if (!id) return
  // 同一行正在提交时忽略连点（按钮此时也已 disabled）
  if (connectorToggling.value) return
  const next = !connectorEnabled(item)
  connectorToggling.value = id
  // 本面板先乐观更新（点下去立即有反馈，不等接口往返）
  applyConnectorState(id, next)
  try {
    await toggleConnector(id, next)
    // 只在真正改成功后才广播：对方收到后会重新拉取，若在请求途中广播，
    // 它拉到的还是旧数据，会出现「跟着变一下又弹回去」的抖动
    emitConnectorsChanged(id, next, 'plus-panel')
    console.log(
      `[${new Date().toISOString()}] [连接器开关] ${item.name} -> ${next ? '启用' : '停用'}`
    )
    showConnectorNotice(
      `已${next ? '启用' : '停用'}「${item.name}」，已同步到「专家·技能·连接器」`
    )
  } catch (e) {
    // 后端没改成功：只回滚本面板（对方从未收到通知，保持原状态即可）
    applyConnectorState(id, !next)
    showConnectorNotice(e.message || '切换连接器开关失败', 'error', 4000)
  } finally {
    connectorToggling.value = ''
  }
}

/** 收起「+」菜单与其二级面板（含待执行的悬停关闭定时器） */
function hidePlusMenu() {
  showPlusMenu.value = false
  subpanelCategory.value = null
  cancelPlusClose()
}

// 会话切换：任何 sessionId 变化都算进入「会话展示阶段」（切历史会话 / 新建会话 /
// 从新建态点开历史会话），令牌一律复位，不得残留到历史会话的输入框里。
// 唯一例外是本组件自己发起的建会话（上传文件 / 切换连接器开关），由对应链路「先存后补」
// （见 ensureSessionId），且不算真正的会话切换、不收起「+」面板。
let selfCreatedSession = false

watch(() => props.sessionId, (newVal, oldVal) => {
  if (newVal === oldVal) return
  // 本组件自己发起的建会话（上传文件 / 切换连接器开关）仍属「输入阶段的连续操作」，
  // 不要收起正在用的「+」面板（否则点一下开关面板就消失，看不出刚做了什么）。
  const ownCreate = selfCreatedSession
  selfCreatedSession = false
  if (!ownCreate) subpanelCategory.value = null
  // 技能标签是文本的一部分：切会话时把标签从草稿里剥掉（等价于旧的「令牌复位」），
  // 避免标签跟着草稿残留到历史会话的输入框里；本组件自己发起的建会话不算切换，保留标签。
  if (!ownCreate) message.value = stripSkillTokens(message.value, tokenMap.value)
  // 注：连接器开关不随会话走（它反映连接器自身的启用状态，与会话无关）
  subpanelKeyword.value = ''
})

// ========== 从本地添加技能（.zip 导入） ==========
function handleSkillImport() {
  skillImportInputRef.value?.click()
}

async function handleSkillImportFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  skillListLoading.value = true
  skillListError.value = ''
  try {
    await importSkill(file)
    await loadSkillSubList()
  } catch (e) {
    skillListError.value = e.message || '导入技能失败'
    skillListLoading.value = false
  }
}

const canSend = computed(() => {
  return (
    message.value.trim() ||
    uploadedFiles.value.length > 0 ||
    selectedSkillDirs.value.length > 0
  )
})

const showTokenPopup = ref(false)

// ========== 会话耗时 ==========
// 后台未返回耗时数据（sessionDuration<=0）且「会话信息」弹窗打开时，前端按 1s
// 间隔本地计时，让会话耗时实时更新；后台有数据时直接用后台值。
const liveDuration = ref(0)
let durationTimer = null
const shouldCountLive = computed(
  () => showTokenPopup.value && (!props.sessionDuration || props.sessionDuration <= 0)
)
const displayDuration = computed(() =>
  shouldCountLive.value ? liveDuration.value : (props.sessionDuration || 0)
)
function startLiveDuration() {
  stopLiveDuration()
  liveDuration.value = 0
  durationTimer = setInterval(() => {
    liveDuration.value += 1
  }, 1000)
}
function stopLiveDuration() {
  if (durationTimer) {
    clearInterval(durationTimer)
    durationTimer = null
  }
}
watch(shouldCountLive, (on) => {
  if (on) startLiveDuration()
  else stopLiveDuration()
})

// 搜索词一变，列表条数就变，面板高度必须跟着重算（底边会随条数上下浮动，
// 变多/变少都要重算；是否关闭由 schedulePlusClose 的「聚焦保护」负责）。
watch(subpanelKeyword, () => nextTick(syncSubpanelBox))
// 技能面板用的是自己的 skillSearch（不是 subpanelKeyword）：漏了这一条，在技能面板里
// 搜索时列表变了但面板高度不重算 —— 表现就是「搜索后高度不跟着收，只有专家面板对」。
watch(skillSearch, () => nextTick(syncSubpanelBox))

const formattedDuration = computed(() => {
  const total = Math.floor(displayDuration.value)
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
})

function toggleTokenPopup() {
  showTokenPopup.value = !showTokenPopup.value
}

function closeTokenPopup() {
  if (showTokenPopup.value) {
    showTokenPopup.value = false
  }
}

const showTokenRing = computed(() => {
  // 首页（尚未进入任何会话）不展示会话级用量：登录/注册后会从全局配置把
  // context_length 写进 sessionUsage，但此时没有任何实际用量，
  // 不能凭这个就显示上下文占用环。
  if (!props.sessionId) return false
  return props.sessionUsage.input_tokens > 0 || props.sessionUsage.output_tokens > 0 || props.sessionUsage.reasoning_tokens > 0 || props.sessionUsage.context_tokens > 0 || props.sessionDuration > 0 || props.iterationCount > 0
})

const contextPercent = computed(() => {
  const u = props.sessionUsage
  if (!u.context_length || u.context_length <= 0) return 0
  // 分子使用「当前轮次的上下文窗口占用」(context_tokens)：即本轮喂给模型的输入 token 数，
  // 与 context_length（上下文窗口上限）对比（已不再统计会话累计总量）。
  const ctxTokens = u.context_tokens || 0
  return Math.min(100, Math.round(ctxTokens / u.context_length * 100))
})

const contextColor = computed(() => {
  const p = contextPercent.value
  if (p >= 80) return '#ef4444'
  if (p >= 50) return '#f59e0b'
  return '#22c55e'
})

const ringRef = ref(null)

const popupStyle = computed(() => {
  if (!ringRef.value) return {}
  const rect = ringRef.value.getBoundingClientRect()
  return {
    position: 'fixed',
    bottom: `${window.innerHeight - rect.top + 10}px`,
    right: `${window.innerWidth - rect.right - 8}px`,
  }
})

function formatTokens(n) {
  if (!n) return '0'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n.toString()
}


/**
 * 确保会话存在并返回 sessionId（并行上传共享同一个创建 Promise，
 * 避免一次选择多个文件时重复触发 createSession）。
 *
 * 本组件自己发起的建会话会让 sessionId 变化：watcher 据此不收起「+」面板、
 * 也不剥离输入框里的技能标签（此刻仍是「用户输入阶段」，文件刚选、技能刚选）。
 * 连接器开关也走这里：新建态下切换开关时先建会话，再按 session_id 提交给后端。
 */
function ensureSessionId() {
  if (props.sessionId) return Promise.resolve(props.sessionId)
  if (!pendingSessionPromise) {
    pendingSessionPromise = new Promise((resolve) => {
      // 创建一个临时的监听器来等待会话创建完成
      const unwatch = watch(
        () => props.sessionId,
        (newSessionId) => {
          if (newSessionId) {
            unwatch()
            resolve(newSessionId)
          }
        }
      )
      // 触发会话创建（标记为「自己发起」，watcher 据此不收起「+」面板）
      selfCreatedSession = true
      emit('createSession')
    }).then((sessionId) => sessionId)
  }
  return pendingSessionPromise
}

/** 单个文件的上传流程（各文件并行执行，互不阻塞） */
async function uploadOneFile(fileItem) {
  const file = fileItem.file
  try {
    const sessionId = await ensureSessionId()
    if (!sessionId) {
      throw new Error('会话创建失败')
    }

    // 等待会话创建期间被用户点 × 删除：不再上传
    if (fileItem.cancelled) return

    // 调用实际的上传接口；holder 用于外部中断（删除上传中的文件）
    const holder = {}
    fileItem.xhrHolder = holder
    const response = await uploadFile(sessionId, file, (progress) => {
      // 找到对应的文件项并更新进度
      if (fileItem.cancelled) return
      const index = uploadedFiles.value.findIndex(f => f.file === file)
      if (index !== -1) {
        uploadedFiles.value[index].uploadProgress = progress
        // 强制触发更新
        uploadedFiles.value = [...uploadedFiles.value]
      }
    }, holder)

    // 上传期间被用户删除：丢弃结果
    if (fileItem.cancelled) return

    // 找到对应的文件项并更新状态
    const index = uploadedFiles.value.findIndex(f => f.file === file)
    if (index !== -1) {
      uploadedFiles.value[index].uploadStatus = 'completed'
      uploadedFiles.value[index].uploadProgress = 100
      uploadedFiles.value[index].filePath = response.file_path
      uploadedFiles.value[index].id = response.id
      // 强制触发更新
      uploadedFiles.value = [...uploadedFiles.value]
    }

    console.log('文件上传成功:', response)
  } catch (error) {
    // 用户主动取消，不算失败
    if (fileItem.cancelled) return
    // 找到对应的文件项并更新状态
    const index = uploadedFiles.value.findIndex(f => f.file === file)
    if (index !== -1) {
      const reason = error?.message || '上传失败'
      // 超过单文件大小限制的失败不给「重试」（重试也没用），其余可重试
      const isSizeLimit = error?.code === 'UPLOAD_FILE_TOO_LARGE' || /不能超过\s*\d+(\.\d+)?\s*MB/.test(reason)
      uploadedFiles.value[index].uploadStatus = 'error'
      uploadedFiles.value[index].errorMessage = reason
      uploadedFiles.value[index].retryable = !isSizeLimit
      // 强制触发更新
      uploadedFiles.value = [...uploadedFiles.value]
    }
    console.error('文件上传失败:', error)
  }
}

/** 重新上传一个失败的文件（复用同一个 File 对象，无需用户重新选择） */
function retryUpload(index) {
  const item = uploadedFiles.value[index]
  if (!item || item.uploadStatus !== 'error' || !item.retryable) return

  // 失败文件不占额度，所以重试等价于「新增一个文件」，同样受限额约束：
  // 已满名额 / 超出剩余空间 / 超过单文件上限 时不允许重新上传
  const L = uploadLimits.value
  if (L.enabled) {
    if (usedCount.value + 1 > L.maxFiles) {
      showUploadNotice(`最多只能上传 ${L.maxFiles} 个文件（已用 ${usedCount.value} 个），无法重新上传 ${item.filename}`)
      return
    }
    if (item.size > mbToBytes(L.maxFileSizeMb)) {
      showUploadNotice(`${item.filename} 超过单文件上限 ${L.maxFileSizeMb}MB，无法重新上传`)
      return
    }
    if (usedBytes.value + item.size > mbToBytes(L.maxTotalMb)) {
      showUploadNotice(`${item.filename} 超出剩余空间 ${formatSize(remainBytes.value)}，无法重新上传`)
      return
    }
  }

  item.uploadStatus = 'uploading'
  item.uploadProgress = 0
  item.errorMessage = ''
  item.retryable = false
  item.cancelled = false
  uploadedFiles.value = [...uploadedFiles.value]
  uploadOneFile(item)
}

async function handleFileSelect(event) {
  const files = Array.from(event.target.files)
  // 先清空 input，避免同一文件二次选择不触发 change
  event.target.value = ''
  if (files.length === 0) return

  // 入列前校验：通过的文件才入列、才上传
  const result = pickAcceptable(files)

  if (result.batchRejected) {
    // 数量不满足：整批拒绝，让用户重新选择
    showUploadNotice(result.message)
    return
  }
  if (result.rejected.length > 0) {
    // 单文件超限 / 超出剩余空间：只跳过这些文件，其余照常上传
    showUploadNotice(buildRejectNotice(result, result.accepted.length))
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
      xhrHolder: null,
      errorMessage: '',
      retryable: false
    }

    // 添加到数组
    uploadedFiles.value.push(fileItem)

    // 强制触发初始渲染
    uploadedFiles.value = [...uploadedFiles.value]

    uploadOneFile(fileItem)
  }
}

async function removeFile(index) {
  const file = uploadedFiles.value[index]
  if (!file) return

  // 标记取消：排队/等待会话中的不再上传，上传中的立即中断请求
  file.cancelled = true
  if (file.uploadStatus === 'uploading' && file.xhrHolder && file.xhrHolder.xhr) {
    try {
      file.xhrHolder.xhr.abort()
    } catch (e) {
      /* 忽略中断异常 */
    }
  }

  // 如果文件已经上传成功，调用后台的删除接口
  if (file.uploadStatus === 'completed' && file.id && props.sessionId) {
    try {
      await deleteFile(props.sessionId, file)
      console.log('文件删除成功:', file.filename)
    } catch (error) {
      console.error('文件删除失败:', error)
    }
  }

  // 从前端列表中移除文件
  uploadedFiles.value.splice(index, 1)
  // 强制触发更新
  uploadedFiles.value = [...uploadedFiles.value]
}

async function send() {
  if (!canSend.value || props.disabled) return
  
  // 等待所有文件上传完成
  const uploadingFiles = uploadedFiles.value.filter(f => f.uploadStatus === 'uploading')
  if (uploadingFiles.length > 0) {
    // 显示上传中提示
    console.log('文件正在上传中，请稍候...')
    // 可以添加一个loading状态或提示信息
    return
  }
  
  // 只发送上传成功的文件：失败文件（无 id/file_path）不随消息发出、不在气泡中展示
  const failedFiles = uploadedFiles.value.filter(f => f.uploadStatus === 'error')
  if (failedFiles.length > 0) {
    console.warn(`[发送消息] ${failedFiles.length} 个文件上传失败，已从本次发送中剔除:`,
      failedFiles.map(f => f.filename).join(', '))
  }
  const filesToSend = uploadedFiles.value
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
  console.log(`[${ts}] [发送消息] 内容: "${message.value.substring(0, 100)}" | 文件数: ${filesToSend.length}`)
  if (filesToSend.length > 0) {
    console.log(`[${ts}] [文件上传] ${filesToSend.map(f => `${f.filename}(${f.size}B)`).join(', ')}`)
  }

  // 已选技能以 skills（技能目录名）参数单独交给后端；输入框里的内联标签只是 UI，
  // 正文发送前会把标签整段剥掉（否则气泡/标题/历史里会露出 `/技能名` 这类暗号）。
  // 连接器不随消息提交：本会话启用哪些连接器由「+」面板的连接器开关单独设置。
  const turnSkills = selectedSkillDirs.value
  // 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：随请求结构化字段上行，
  // 后端校验归属后绑定为 save_expert 的修改目标
  const expertEditId = pendingExpertEditId.value
  emit(
    'send',
    buildPayload(),
    filesToSend,
    null,
    true,
    webSearchEnabled.value,
    turnSkills,
    expertEditId
  )

  message.value = ''
  uploadedFiles.value = []
  // 预选技能与待修改专家已随本条消息提交，清掉避免影响下一条
  preselectedSkillDirs.value = new Set()
  pendingExpertEditId.value = ''
  dismissUploadNotice()
  closeSkillPicker()
  nextTick(() => autoResize())
}

/**
 * 发送的消息正文：剥掉内联技能标签、只归一化用户输入，不拼任何指令前缀。
 * 已选技能以 skills（目录名）参数带给后端（见 send()）。
 */
function buildPayload() {
  return buildMessageText(stripSkillTokens(message.value, tokenMap.value))
}

function stop() {
  emit('stop')
}

function autoResize() {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto'
    textareaRef.value.style.height = Math.min(textareaRef.value.scrollHeight, TEXTAREA_MAX_HEIGHT) + 'px'
    syncMirror() // 高度变化会改变 textarea 的可滚动区间（scrollTop 被钳制），镜像层要跟上
  }
}

function onInput(event) {
  const ta = event?.target || textareaRef.value
  // 移动端兜底（见 utils/skillPillTokens.js 的 reconcileTokenDeletion）：Android Chrome 的
  // Backspace 不给 keydown、部分浏览器也没有 beforeinput，浏览器只啃掉标签的一两个字时，
  // 在这里按「编辑前/后文本差分」把整个标签补删干净，避免留下半个标签的残缺文本。
  if (ta && typeof ta.value === 'string') {
    const healed = reconcileTokenDeletion(lastInputText, ta.value, ta.selectionStart, tokenMap.value)
    if (healed) {
      // replaceTextWithCaret 会复位光标并同步镜像层；lastInputText 由 syncMirror 更新
      replaceTextWithCaret(healed)
      emit('typing')
      return
    }
  }
  autoResize()
  updateCaretLine()
  updateSkillPicker()
  syncMirror()
  emit('typing')
}

watch(message, (val) => {
  try {
    sessionStorage.setItem(props.draftKey, val)
  } catch (e) {
    // 隐私模式等场景下 sessionStorage 可能不可用，忽略即可
  }
})

// 程序化赋值（恢复草稿、切会话、发送后清空）不触发 input 事件，镜像层在这里补同步
watch(message, () => nextTick(syncMirror))

watch(() => props.disabled, (val) => {
  if (!val && textareaRef.value) {
    textareaRef.value.focus()
  }
})

onMounted(() => {
  // 恢复刷新前的输入草稿
  try {
    const draft = sessionStorage.getItem(props.draftKey)
    if (draft) {
      message.value = draft
      nextTick(() => autoResize())
    }
  } catch (e) {
    // 忽略草稿恢复失败
  }

  // 技能列表先拉一次：草稿里可能留着技能标签，需要靠列表把 `／显示名` 反解回技能
  loadSkillOptions()
    .then(() => {
      // 旧版草稿里的半角 / 标签迁移成全角形式（要等技能列表就绪才能识别旧 token）
      if (message.value) {
        const migrated = migrateLegacySkillTokens(message.value, tokenMap.value)
        if (migrated !== message.value) message.value = migrated
      }
    })
    .catch(() => { /* 列表拉取失败时标签按普通文本显示，不影响输入 */ })

  // 恢复待选技能（技能中心「创建技能」等入口预置），读取后即清除
  if (props.consumePendingSkill) restorePendingSkill()

  // 消费待修改专家（专家面板「编辑」预置）：ID 存组件状态、不进正文，发送时上行
  try {
    const rawEdit = sessionStorage.getItem(PENDING_EXPERT_EDIT_KEY)
    if (rawEdit) sessionStorage.removeItem(PENDING_EXPERT_EDIT_KEY)
    const parsed = rawEdit ? JSON.parse(rawEdit) : null
    if (parsed?.expert_agent_id) pendingExpertEditId.value = parsed.expert_agent_id
  } catch (_) { /* 隐私模式等场景忽略 */ }

  syncMirror()
  document.addEventListener('click', closeTokenPopup)
  document.addEventListener('click', closeSkillPickerOnClick)
  window.addEventListener('resize', handleViewportChange)
  document.addEventListener('click', closePlusMenu)
  loadWebSearchStatus()
  loadKnowledgeStatus()
  // 「专家·技能·连接器」面板里改了开关 → 本面板同步（两个面板不在同一组件树分支，走事件广播）
  offConnectorsChanged = onConnectorsChanged(onConnectorsChangedHandler)
  loadUploadLimits()
})

onUnmounted(() => {
  document.removeEventListener('click', closeTokenPopup)
  document.removeEventListener('click', closeSkillPickerOnClick)
  window.removeEventListener('resize', handleViewportChange)
  document.removeEventListener('click', closePlusMenu)
  if (offConnectorsChanged) offConnectorsChanged()
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer)
  cancelPlusClose()
  stopLiveDuration()
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer)
})

// 供父组件把预置问题完整文本填进输入框（卡片上最多显示 3 行，长问题展示不全）
function setMessage(text) {
  message.value = text || ''
  nextTick(() => textareaRef.value?.focus())
}

function getMessage() {
  return message.value
}

// 供父组件（预置问题卡片点击）走输入框的正常发送链路：复用 send() 的
// 上传等待、canSend/disabled 校验与清空逻辑，不重复实现一份发送
function triggerSend() {
  send()
}

// 供父组件调用：聚焦输入框并把光标移到末尾（如侧边栏打开时）
function focusInput() {
  nextTick(() => {
    const ta = textareaRef.value
    if (!ta || ta.disabled) return
    ta.focus()
    ta.setSelectionRange(ta.value.length, ta.value.length)
  })
}

// 供父组件调用：替换输入内容（如侧边栏的快捷提问、划词提问）
function setDraft(text) {
  message.value = text || ''
  nextTick(() => autoResize())
  focusInput()
}

defineExpose({ setMessage, getMessage, triggerSend, focusInput, setDraft })
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


/* ── 内联技能标签（`/显示名`，发送时整段剥掉，技能走 skills 字段） ── */
/* 镜像渲染层：与 textarea 同字体/字号/行高/宽度，两侧文字换行位置才会逐行一致。
   叠在 textarea 之上但 pointer-events:none，输入、光标、选区仍由下层 textarea 承担；
   break-spaces + break-word 对齐 textarea 的默认换行规则（含行尾空格折行）。 */
.input-mirror {
  position: absolute;
  inset: 0;
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
.plus-list-item.connector-row {
  cursor: default;
}

.connector-switch {
  position: relative;
  flex-shrink: 0;
  width: 34px;
  height: 18px;
  margin-left: 8px;
  padding: 0;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  background: var(--bg-tertiary);
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.connector-switch .connector-switch-knob {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.25);
  transition: transform 0.15s ease;
}

.connector-switch.on {
  background: var(--accent-color);
  border-color: var(--accent-color);
}

.connector-switch.on .connector-switch-knob {
  transform: translateX(16px);
}

.connector-switch:focus-visible {
  outline: 2px solid var(--accent-color);
  outline-offset: 2px;
}

/* 提交中：明确禁止态（正常状态下开关任何一端都能点） */
.connector-switch:disabled {
  cursor: not-allowed;
}

.connector-switch.is-off:disabled {
  opacity: 0.55;
}

/* 正在提交：开关中心转圈 */
.connector-switch-spinner {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 10px;
  height: 10px;
  margin: -5px 0 0 -5px;
  border: 1.5px solid var(--text-secondary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: connector-switch-spin 0.6s linear infinite;
}

@keyframes connector-switch-spin {
  to {
    transform: rotate(360deg);
  }
}

.connector-switch.is-busy .connector-switch-knob {
  opacity: 0.35;
}

/* 连接器面板内的操作反馈条（改完开关提示一句） */


html[data-theme="dark"] .skill-sub-notice {
  background: #27272a;
  color: #a1a1aa;
}

html[data-theme="dark"] .skill-sub-notice.error {
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

/* 面板头部右侧的搜索按钮（连接器面板） */
.plus-subpanel-search-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  margin-left: auto;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-subpanel-search-btn:hover,
.plus-subpanel-search-btn.active {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.plus-subpanel-search-btn svg {
  width: 14px;
  height: 14px;
}

/* ── 技能选择面板（Teleport 到 body，样式不能依赖父级作用域） ── */
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
     否则内容会撑破面板被 outer overflow: hidden 裁掉，滚动条也随之失效 */
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
  gap: 10px;
}



/* 星形图标：给「模型选择」一个视觉锚点，避免三个按钮里只有它是纯文字 */










/* 键盘可达性：工具按钮共用一致的焦点环 */
.plus-btn:focus-visible,
.web-search-btn:focus-visible,
.send-btn:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 2px;
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
  /* 单行布局：图标与文字垂直居中（两行布局时是 flex-start） */
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  transition: background 0.15s ease;
}

.plus-list-item:hover {
  background: var(--bg-tertiary);
}

/* 可点选行（连接器）：加手型 + 已选态整行高亮（再点一次取消） */
.plus-list-item.selectable {
  cursor: pointer;
}

.plus-list-item.selected {
  background: color-mix(in srgb, var(--accent-color) 14%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.plus-list-state {
  flex-shrink: 0;
  align-self: center;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-color);
  color: #fff;
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

/* 名称 / 标签 / 描述同排：与专家面板 .plus-expert-main 同一套对齐方式。
   name 与 tag 不收缩（名字读全），desc 吃掉剩余宽度并省略号。 */
.plus-list-body {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.plus-list-name {
  flex-shrink: 0;
  max-width: 46%;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-list-tag {
  flex-shrink: 0;
  /* baseline 对齐时小标签会偏上，单独拉回垂直居中 */
  align-self: center;
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
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  /* 单行：line-clamp 在 row 布局下不生效，改用 nowrap + 省略号 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

/* ========== 子面板常驻搜索框（专家 / 连接器） ==========
   技能面板的同类样式在 SkillPicker.vue 的 scoped style 里，这里需自带一份。 */
.skill-sub-search {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 4px 8px;
  padding: 7px 10px;
  border-radius: 10px;
  background: var(--bg-tertiary);
  border: 1px solid transparent;
  transition: border-color 0.15s ease;
}

.skill-sub-search:focus-within {
  border-color: color-mix(in srgb, var(--accent-color) 45%, transparent);
}

.skill-sub-search-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.skill-sub-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  line-height: 1.3;
}

.skill-sub-search-input::placeholder {
  color: var(--text-secondary);
}

.skill-sub-search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.skill-sub-search-clear:hover {
  color: var(--text-primary);
}

.skill-sub-search-clear svg {
  width: 12px;
  height: 12px;
}




/* 专家面板：上区「我的专家」+ 浅色分隔线 + 下区「公共专家」 */
/* 容纳 3 + 3 行（约 320px）：超出则滚动，滚到底自动放出下一批 */

.plus-experts-section {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 6px;
}

.plus-experts-section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.plus-experts-section-count {
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.plus-experts-badge {
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 10.5px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.plus-experts-divider {
  height: 1px;
  margin: 8px 4px;
  background: var(--border-color);
}

.plus-expert-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  /* 本行是 <button>，浏览器默认不继承 font-family（会退回 Arial / 宋体），
     而连接器行是 <div> 会继承 —— 不写这一行两行文字字体就不一致。 */
  font-family: inherit;
  /* 与技能面板 .plus-list-item 的行内边距保持一致 */
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.plus-expert-row:hover {
  background: var(--bg-tertiary);
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
  /* 尺寸/字号/字重与技能面板 .plus-list-icon（initial 变体）一致 */
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  /* 底色/字色由全局 .ea-0 ~ .ea-7 提供（按专家 ID 稳定取档）；此处不写死，
     否则 scoped 规则会与全局色板同权重竞争、按注入顺序取胜而覆盖配色。 */
}

/* 头像配色已收敛到全局 .ea-0 ~ .ea-7（style.css + utils/expertAvatar.js）：
   按 export_agent_id 稳定取档，技能中心 / 下拉 / 胶囊 / 消息标识四处同色。
   这里不再保留按序号取色的 av-c0..av-c7，避免两套色板并存。 */

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
  /* 字体样式与技能面板 .plus-list-name 一致 */
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.plus-expert-desc {
  flex: 1;
  min-width: 0;
  /* 字体样式与技能面板 .plus-list-desc 一致 */
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 名称行：默认不占盒（名称与描述同排）；AI 侧边栏（compact）下改为名称 +「已选」一行、描述一行 */
.plus-expert-name-row {
  display: contents;
}

.plus-experts.stacked .plus-expert-row {
  align-items: flex-start;
}

.plus-experts.stacked .plus-expert-main {
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
}

.plus-experts.stacked .plus-expert-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.plus-experts.stacked .plus-expert-name {
  flex: 1;
  max-width: none;
}

.plus-experts.stacked .plus-expert-desc {
  line-height: 1.5;
}

.plus-expert-state {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 5px;
  font-size: 11px;
  font-weight: 500;
  background: var(--accent-color);
  color: #fff;
}

.plus-expert-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 8px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 默认与提示文字同色，悬停才变强调色（不做成显眼的链接） */
.plus-expert-create {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-expert-create:hover {
  color: var(--accent-color);
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
  color: var(--text-secondary);
  text-align: center;
  cursor: pointer;
}

.plus-expert-more:hover {
  background: var(--bg-tertiary);
  color: var(--accent-color);
}




/* 停用技能不可挂载：Agent 只加载已启用的技能，挂上去也不会生效 */










/* 三段子面板（专家 / 连接器 / 技能）共用同一个外框。
   技能面板的内容在 SkillPicker.vue 里，但**外框必须由本组件渲染**：
   外框带了内联的 top/height，syncSubpanelBox 需要能量到"内容自然高度"，
   而子组件 prop 控制的样式会与量高互相打架（2026-10-24 技能面板搜索后
   高度不收缩就是这么来的）。宽度由 openSubpanel 通过 SUBPANEL_WIDTH
   以内联样式给出（与 JS 定位同源）。 */
.skill-submenu.plus-subpanel {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.14);
  padding: 12px;
  z-index: 9999;
  max-width: 90vw;
}

/* 热桥：面板与一级菜单之间有 6px 水平间隙，鼠标横移时两边都不属于，会先触发
   mouseleave 计时。向左延 7px（覆盖间隙 + 1px 重叠）把这段空白划给面板。
   面板底边最低与一级菜单底边平齐（内容少了会往上收），所以再向下延 28px 把
   「右上角接缝」也划进面板，从菜单项斜着往左上角挪时不会中途掉出去。 */
.skill-submenu.plus-subpanel::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: -28px;
  left: -7px;
  width: 7px;
}

/* 专家 / 连接器列表容器：.skill-sub-list 的样式原先只写在 SkillPicker.vue 的
   scoped block 里，作用不到本组件渲染的元素（与上面的 .skill-submenu 同一个坑），
   结果列表没有 overflow —— 面板超过视口时内容会溢出圆角框外。
   高度策略：列表不自带上限，由面板统一控制（syncSubpanelBox 按内容自然高度给
   height，封顶 SUBPANEL_MAX_HEIGHT），列表 flex 撑满剩余空间，超了才滚动；
   min-height:0 是让 flex 能真正压缩列表的关键。 */
.skill-sub-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  scrollbar-width: thin;
}

/* 空态 / 无结果提示：面板高度被锁定时列表区可能很高，单条提示靠垂直居中显示，
   看起来才像「有意的空状态」而不是「内容没加载出来」。 */
.skill-sub-list > .skill-sub-hint:only-child,
.skill-sub-list > .plus-list-hint:only-child {
  margin: auto 0;
}

/* ⚠️ 专家 / 连接器面板的空态样式必须在这里再写一份。
   `.skill-sub-hint` 的完整规则原先只存在于 SkillPicker.vue 的 <style scoped> 里，
   而 scoped 不跨组件 —— SkillPicker 渲染的技能面板能吃到，本组件渲染的专家 /
   连接器面板吃不到，表现为「加载中…」「未找到匹配的连接器」是浏览器默认样式：
   左对齐、深色、无内边距（2026-10-24 用户截图报障）。
   规格与技能面板完全一致，改一处要两处同步。 */
.skill-sub-hint {
  padding: 16px 10px;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
}

.skill-sub-hint.error {
  color: #dc2626;
}

/* 标题行 / 搜索框 / 底栏不参与压缩，只有列表可伸缩 */
.plus-subpanel-head,
.skill-sub-search,
.skill-sub-footer {
  flex: 0 0 auto;
}

/* 专家/连接器子面板：标题行（无返回按钮，主菜单常驻在旁边） */
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
  color: var(--text-primary);
}

/* 底栏与操作反馈：这三段样式原先只写在 SkillPicker.vue 的 scoped block 里，
   作用不到本组件渲染的专家 / 连接器面板（底栏按钮曾是无样式的浏览器默认按钮）。
   规格与技能面板保持一致。 */
.skill-sub-footer {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}

.skill-sub-footer-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.skill-sub-footer-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.skill-sub-footer-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
}

.skill-sub-notice {
  margin: 6px 10px 0;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  flex: 0 0 auto;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.skill-sub-notice.error {
  background: rgba(220, 38, 38, 0.08);
  color: #dc2626;
}





/* ========== 输入框内的技能令牌 ========== */
.skill-tokens {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.skill-token {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px 3px 4px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-tertiary));
  border: 1px solid color-mix(in srgb, var(--accent-color) 28%, transparent);
  font-size: 15px; /* 与输入框 textarea 正文同字号 */
  line-height: 1.5;
  color: var(--text-primary);
}

.skill-token-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.skill-token-emoji {
  font-size: 12px;
  line-height: 1;
  font-family: "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif;
}

.skill-token-name {
  max-width: 180px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

.skill-token-remove {
  display: flex;
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
  transition: background 0.15s, color 0.15s;
}

.skill-token-remove:hover {
  background: color-mix(in srgb, var(--text-primary) 12%, transparent);
  color: var(--text-primary);
}

.skill-token-remove svg {
  width: 11px;
  height: 11px;
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
  transition: transform 0.2s;
}

.context-ring-wrapper:hover {
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
