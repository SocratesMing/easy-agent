<template>
  <div class="chat-input-container">
    <div class="input-box" ref="inputBoxRef">
      <!-- 上传限额汇总提示（整批拒绝 / 文件被跳过时出现，2.6s 自动消失） -->
      <div
        v-if="uploadNotice"
        class="upload-notice"
        role="status"
        aria-live="polite"
      >
        <span class="upload-notice-text">{{ uploadNotice }}</span>
        <button
          type="button"
          class="upload-notice-close"
          aria-label="关闭提示"
          @click="dismissUploadNotice"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
          >
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
      <div v-if="fileReferences.length" class="reference-tags">
        <FileReferenceTag
          v-for="ref in fileReferences"
          :key="ref.path"
          :item="ref"
          removable
          @remove="removeReference(ref)"
        />
      </div>
      <!-- 本轮输入单独就可能撑爆模型窗口：压缩历史（/compact）对它无效，
           改为存成工作区 txt 再引用，让模型按需分块读取 -->
      <div v-if="longMessageHint" class="long-message-hint" role="status">
        <svg
          class="long-message-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path
            d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
          />
          <path d="M14 2v6h6" />
        </svg>
        <span class="long-message-text">
          本轮输入约 {{ longMessageWords }}，<strong>可能超出模型上下文窗口</strong>
          <!-- <span v-if="longMessageUsagePercent" class="long-message-meta"
            >（窗口 {{ longMessageWindow }}，已用
            {{ longMessageUsagePercent }}%）</span> -->
        </span>
        <button
          type="button"
          class="long-message-action"
          :disabled="offloadingLongMessage"
          @click="offloadLongMessage"
        >
          {{ offloadingLongMessage ? "正在保存…" : "存为 txt 并引用" }}
        </button>
        <button
          type="button"
          class="long-message-dismiss"
          aria-label="关闭提示"
          @click="longMessageDismissed = true"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <FileReferencePicker
        ref="referencePicker"
        :open="referenceOpen"
        :session-id="sessionId || ''"
        :load-tree="getWorkspaceTree"
        :query="referenceSearch"
        :style="skillPickerStyle"
        @navigate="resetReferenceQuery"
        @select="selectReference"
        @close="closeReference"
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
          <div
            v-show="mirrorText"
            ref="mirrorRef"
            class="input-mirror"
            aria-hidden="true"
          >
            <template v-for="(seg, index) in mirrorSegments" :key="index"
              ><span v-if="seg.type === 'text'" class="input-mirror-text">{{
                seg.text
              }}</span
              ><span
                v-else
                class="input-skill-pill"
                :title="`${seg.name}（点击移除）`"
                @click.stop.prevent="removeSkillToken(seg.dirName)"
                ><span class="input-skill-pill-mark"
                  >／<SkillIcon
                    :icon="seg.icon"
                    :category="iconCategoryFor(seg.categoryId)"
                    size="xs" /></span
                >{{ seg.label }}</span
              ></template
            >
          </div>
          <textarea
            ref="textareaRef"
            v-model="message"
            spellcheck="false"
            v-tip.hover="
              '输入消息，Enter 发送 · Shift+Enter 换行 · 输入 / 选择技能 · 输入 @ 引用会话文件'
            "
            @keydown="onKeydown"
            @beforeinput="onBeforeInput"
            @paste="invalidateEnhancement"
            @compositionstart="enhancementCompositionStart"
            @compositionend="enhancementCompositionEnd"
            @input="onInput"
            @focus="updateCaretLine"
            @blur="hideCaretLine"
            @keyup="updateCaretLine"
            @click="updateCaretLine"
            @scroll="updateCaretLine"
            placeholder="今天帮你做些什么？@ 引用会话文件，/ 调用技能与指令"
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
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
            <Teleport to="body">
              <div
                v-if="showPlusMenu"
                ref="plusMenuListRef"
                class="plus-menu"
                :style="plusMenuStyle"
                @click.stop
                @mouseenter="cancelPlusClose"
                @mouseleave="schedulePlusClose"
              >
                <button
                  type="button"
                  class="plus-menu-item"
                  @mouseenter="hoverPlusItem('file')"
                  @click="handleMenuAddFile"
                >
                  <svg
                    class="plus-menu-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path
                      d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
                    ></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="12" y1="18" x2="12" y2="12"></line>
                    <line x1="9" y1="15" x2="15" y2="15"></line>
                  </svg>
                  <span class="plus-menu-label">添加文件</span>
                  <svg
                    class="plus-menu-arrow"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button
                  v-if="knowledgeEnabled"
                  type="button"
                  class="plus-menu-item plus-menu-item-knowledge"
                  @mouseenter="hoverPlusItem('knowledge', $event)"
                  @click="openKnowledgeScope($event)"
                >
                  <svg
                    class="plus-menu-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
                    <path
                      d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"
                    ></path>
                  </svg>
                  <span class="plus-menu-label">知识库</span>
                  <svg
                    class="plus-menu-arrow"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button
                  type="button"
                  class="plus-menu-item"
                  @mouseenter="hoverPlusItem('experts', $event)"
                  @click="openSubpanel('experts', $event)"
                >
                  <svg
                    class="plus-menu-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                    <circle cx="12" cy="7" r="4"></circle>
                  </svg>
                  <span class="plus-menu-label">专家</span>
                  <svg
                    class="plus-menu-arrow"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button
                  type="button"
                  class="plus-menu-item"
                  @mouseenter="hoverPlusItem('skills', $event)"
                  @click="openSubpanel('skills', $event)"
                >
                  <svg
                    class="plus-menu-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polygon
                      points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"
                    ></polygon>
                  </svg>
                  <span class="plus-menu-label">技能</span>
                  <svg
                    class="plus-menu-arrow"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="9 18 15 12 9 6"></polyline>
                  </svg>
                </button>
                <button
                  type="button"
                  class="plus-menu-item"
                  data-category="connectors"
                  @mouseenter="hoverPlusItem('connectors', $event)"
                  @click="openSubpanel('connectors', $event)"
                >
                  <svg
                    class="plus-menu-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M12 22v-5"></path>
                    <path d="M9 8V2"></path>
                    <path d="M15 8V2"></path>
                    <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"></path>
                  </svg>
                  <span class="plus-menu-label">连接器</span>
                  <svg
                    class="plus-menu-arrow"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
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
              :pending-base-ids="pendingKnowledgeBaseIds"
              hide-trigger
              anchor-selector=".plus-menu-item-knowledge"
              @change="handleKnowledgeScopeChange"
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
                  <svg
                    class="skill-sub-search-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
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
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                    >
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
                <div v-if="plusListLoading" class="skill-sub-hint">加载中…</div>
                <div v-else-if="plusListError" class="skill-sub-hint error">
                  {{ plusListError }}
                </div>
                <div
                  v-else
                  class="skill-sub-list plus-experts"
                  :class="{ stacked: compact }"
                >
                  <!-- 上区：我的专家（服务端按 status=active 下发，且已按关键字过滤 + 分页）。
                       有搜索词时清空的区连标题一起收起，避免「标题 + 空白」的空壳；
                       搜索请求在飞时保留标题，否则它会闪一下再出现。 -->
                  <div v-if="showMineSection" class="plus-experts-section">
                    <span class="plus-experts-section-label">我的专家</span>
                    <span
                      v-if="myExpertsTotal"
                      class="plus-experts-section-count"
                      >{{ myExpertsTotal }}</span
                    >
                  </div>
                  <button
                    v-for="item in myExperts"
                    :key="`mine:${item.export_agent_id}`"
                    type="button"
                    class="plus-expert-row"
                    :class="{ selected: isExpertSelected(item) }"
                    :title="
                      isExpertSelected(item)
                        ? '再次点击取消召唤'
                        : `召唤「${item.export_name_cn}」`
                    "
                    @click="toggleExpert(item)"
                  >
                    <span
                      class="plus-expert-avatar"
                      :class="expertAvatarClass(item)"
                      >{{ expertAvatarChar(item) }}</span
                    >
                    <span class="plus-expert-main">
                      <span class="plus-expert-name-row">
                        <span class="plus-expert-name">{{
                          item.export_name_cn
                        }}</span>
                        <span
                          v-if="compact && isExpertSelected(item)"
                          class="plus-expert-state"
                          >已选</span
                        >
                      </span>
                      <span class="plus-expert-desc">{{
                        expertDesc(item)
                      }}</span>
                    </span>
                    <span
                      v-if="!compact && isExpertSelected(item)"
                      class="plus-expert-state"
                      >已选</span
                    >
                  </button>
                  <div
                    v-if="myExpertsLoading && !myExperts.length"
                    class="skill-sub-hint"
                  >
                    加载中…
                  </div>
                  <div
                    v-if="
                      !subpanelKeyword &&
                      !myExperts.length &&
                      !myExpertsLoading &&
                      !myExpertsError
                    "
                    class="plus-expert-empty"
                  >
                    <span class="plus-expert-empty-text"
                      >您还没有创建自己的专家</span
                    >
                    <button
                      type="button"
                      class="plus-expert-create"
                      @click="handleMenuPanel('experts')"
                    >
                      「去创建」
                    </button>
                  </div>
                  <!-- 加载更多：只在「已加载 < 总数」时出现，全部加载完就不再展示 -->
                  <button
                    v-if="hasMoreMine"
                    type="button"
                    class="plus-expert-more"
                    :disabled="myExpertsLoading"
                    @click="loadMoreMyExperts"
                  >
                    {{
                      myExpertsLoading
                        ? "加载中…"
                        : myExpertsError
                          ? "加载失败，点击重试"
                          : "点击加载更多"
                    }}
                  </button>
                  <div v-else-if="myExpertsError" class="skill-sub-hint error">
                    {{ myExpertsError }}
                  </div>

                  <div
                    v-if="showMineSection && showPublicSection"
                    class="plus-experts-divider"
                  ></div>

                  <!-- 下区：公共（预置）专家 -->
                  <div v-if="showPublicSection" class="plus-experts-section">
                    <span class="plus-experts-section-label">公共专家</span>
                    <span class="plus-experts-badge">预置</span>
                    <span
                      v-if="filteredPublicExperts.length"
                      class="plus-experts-section-count"
                      >{{ filteredPublicExperts.length }}</span
                    >
                  </div>
                  <button
                    v-for="item in visiblePublicExperts"
                    :key="`public:${item.export_agent_id}`"
                    type="button"
                    class="plus-expert-row"
                    :class="{ selected: isExpertSelected(item) }"
                    :title="
                      isExpertSelected(item)
                        ? '再次点击取消召唤'
                        : `召唤「${item.export_name_cn}」`
                    "
                    @click="toggleExpert(item)"
                  >
                    <span
                      class="plus-expert-avatar"
                      :class="expertAvatarClass(item)"
                      >{{ expertAvatarChar(item) }}</span
                    >
                    <span class="plus-expert-main">
                      <span class="plus-expert-name-row">
                        <span class="plus-expert-name">{{
                          item.export_name_cn
                        }}</span>
                        <span
                          v-if="compact && isExpertSelected(item)"
                          class="plus-expert-state"
                          >已选</span
                        >
                      </span>
                      <span class="plus-expert-desc">{{
                        expertDesc(item)
                      }}</span>
                    </span>
                    <span
                      v-if="!compact && isExpertSelected(item)"
                      class="plus-expert-state"
                      >已选</span
                    >
                  </button>
                  <div
                    v-if="!subpanelKeyword && !filteredPublicExperts.length"
                    class="skill-sub-hint"
                  >
                    公共专家目录为空
                  </div>
                  <!-- 搜索无命中：两区都空时给一句统一提示；我的专家还在查时先不提示 -->
                  <div
                    v-if="
                      subpanelKeyword &&
                      !myExperts.length &&
                      !filteredPublicExperts.length &&
                      !myExpertsLoading
                    "
                    class="skill-sub-hint"
                  >
                    未找到匹配的专家
                  </div>
                  <!-- 公共专家是本地分页（目录全量下发），与「我的专家」的服务端分页互不影响 -->
                  <button
                    v-if="
                      filteredPublicExperts.length > visiblePublicExperts.length
                    "
                    type="button"
                    class="plus-expert-more"
                    @click="loadMorePublicExperts"
                  >
                    还有
                    {{
                      filteredPublicExperts.length - visiblePublicExperts.length
                    }}
                    位 · 点击加载
                  </button>
                </div>
                <div v-if="showManageEntry" class="skill-sub-footer">
                  <button
                    type="button"
                    class="skill-sub-footer-btn"
                    @click="handleMenuPanel('experts')"
                  >
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
                  <svg
                    class="skill-sub-search-icon"
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
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
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                    >
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>
                <div class="skill-sub-list">
                  <div v-if="plusListLoading" class="skill-sub-hint">
                    加载中…
                  </div>
                  <div v-else-if="plusListError" class="skill-sub-hint error">
                    {{ plusListError }}
                  </div>
                  <div v-else-if="plusList.length === 0" class="skill-sub-hint">
                    {{ plusListEmptyText }}
                  </div>
                  <div
                    v-else-if="filteredPlusList.length === 0"
                    class="skill-sub-hint"
                  >
                    未找到匹配的连接器
                  </div>
                  <div
                    v-else
                    v-for="item in filteredPlusList"
                    :key="item.key"
                    class="plus-list-item connector-row"
                  >
                    <span
                      class="plus-list-icon"
                      :class="{ initial: !item.icon }"
                      >{{ item.icon || item.initial }}</span
                    >
                    <span class="plus-list-body">
                      <span class="plus-list-name">{{ item.name }}</span>
                      <span
                        v-if="item.tag"
                        class="plus-list-tag"
                        :class="item.tagType"
                        >{{ item.tag }}</span
                      >
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
                      <span
                        v-if="isConnectorBusy(item)"
                        class="connector-switch-spinner"
                      ></span>
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
                  <button
                    type="button"
                    class="skill-sub-footer-btn"
                    @click="handleMenuPanel(subpanelCategory)"
                  >
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
            :class="{
              active: webSearchEnabled,
              disabled: webSearchUnavailable || isStreaming || disabled,
            }"
            :disabled="webSearchUnavailable || isStreaming || disabled"
            v-tip="webSearchTitle"
            @click="toggleWebSearch"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="2" y1="12" x2="22" y2="12"></line>
              <path
                d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"
              ></path>
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
            <span
              class="expert-chip-avatar"
              :class="expertAvatarClass(activeExpert)"
              >{{ expertAvatarChar(activeExpert) }}</span
            >
            <span class="expert-chip-name">{{
              activeExpert.export_name_cn
            }}</span>
            <button
              type="button"
              class="expert-chip-clear"
              :disabled="isStreaming || disabled"
              aria-label="退出专家模式"
              @click="emit('clearExpert')"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.5"
                aria-hidden="true"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        <div class="right-actions">
          <PromptEnhancementActions
            :enabled="canEnhance"
            :state="enhancement"
            @enhance="enhancePrompt"
          />
          <div
            v-if="showTokenRing"
            ref="ringRef"
            class="context-ring-wrapper"
            @click.stop="toggleTokenPopup"
          >
            <svg class="context-ring" viewBox="0 0 36 36">
              <circle
                class="context-ring-bg"
                cx="18"
                cy="18"
                r="15.9"
                fill="none"
                stroke="#e5e7eb"
                stroke-width="3"
              />
              <circle
                class="context-ring-fill"
                cx="18"
                cy="18"
                r="15.9"
                fill="none"
                :stroke="contextColor"
                stroke-width="3"
                stroke-linecap="round"
                :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
                transform="rotate(-90 18 18)"
              />
            </svg>
            <span class="context-ring-text">{{ contextPercent }}%</span>
            <Teleport to="body">
              <div
                v-if="showTokenPopup"
                class="token-popup"
                :style="popupStyle"
                @click.stop
              >
                <div class="token-popup-title">会话信息</div>
                <div class="token-popup-section">
                  <div class="token-popup-row">
                    <span class="token-popup-label">会话耗时</span>
                    <span class="token-popup-value duration-value">{{
                      formattedDuration
                    }}</span>
                  </div>
                  <div class="token-popup-row">
                    <span class="token-popup-label">迭代次数</span>
                    <span class="token-popup-value">{{ iterationCount }}</span>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-section">
                  <div class="token-popup-row">
                    <span class="token-popup-label"
                      >本轮上下文占用{{
                        sessionUsage.context_estimated ? "（估算）" : ""
                      }}</span
                    >
                  </div>
                  <div class="token-popup-context-row">
                    <span class="token-popup-context-value"
                      >{{ formatTokens(sessionUsage.context_tokens) }}/{{
                        formatTokens(sessionUsage.context_length)
                      }}</span
                    >
                    <span
                      class="token-popup-context-percent"
                      :style="{ color: contextColor }"
                      >{{ contextPercent }}%</span
                    >
                  </div>
                  <div class="token-popup-bar">
                    <div class="token-popup-bar-inner">
                      <div
                        class="token-popup-bar-fill"
                        :style="{
                          width: contextPercent + '%',
                          background: contextColor,
                        }"
                      ></div>
                    </div>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-row">
                  <span class="token-popup-label">输入 Token</span>
                  <span class="token-popup-value input">{{
                    formatTokens(sessionUsage.input_tokens)
                  }}</span>
                </div>
                <div class="token-popup-row">
                  <span class="token-popup-label">输出 Token</span>
                  <span class="token-popup-value output">{{
                    formatTokens(sessionUsage.output_tokens)
                  }}</span>
                </div>
                <div class="token-popup-row">
                  <span class="token-popup-label">思考 Token</span>
                  <span class="token-popup-value reasoning">{{
                    formatTokens(sessionUsage.reasoning_tokens)
                  }}</span>
                </div>
              </div>
            </Teleport>
          </div>
          <button
            v-if="!isStreaming"
            @click="send"
            class="action-btn send-btn"
            :class="{ active: canSend }"
            :disabled="
              !canSend || disabled || preparingFiles || enhancement.pending
            "
            v-tip="'发送（Enter）'"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
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
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="currentColor"
            >
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
          <span class="skill-picker-hint">↑↓ 选择 · Enter 确认 · Esc 关闭</span>
        </div>

        <div v-if="skillPickerLoading" class="skill-picker-state">
          正在加载技能…
        </div>
        <div
          v-if="menuItems.length === 0 && !skillPickerLoading"
          class="skill-picker-state"
        >
          {{
            skillOptions.length === 0
              ? "没有匹配项，可到技能中心添加并启用技能"
              : "没有匹配的指令或技能"
          }}
        </div>

        <div
          v-if="menuItems.length"
          class="skill-picker-list"
          role="listbox"
          aria-label="指令和技能"
        >
          <ul
            v-for="group in pickerGroups"
            :key="group.title"
            class="picker-group"
            role="group"
            :aria-label="group.title"
          >
            <li class="picker-group-title" role="presentation">
              {{ group.title }} ({{ group.rows.length }})
            </li>
            <li
              v-for="row in group.rows"
              :key="
                (row.skill.command ? 'command:' : 'skill:') + row.skill.dir_name
              "
              class="skill-picker-item"
              :class="{
                active: row.index === skillPickerIndex,
                unavailable: row.skill.disabled,
              }"
              :aria-disabled="row.skill.disabled ? 'true' : 'false'"
              role="option"
              :aria-selected="row.index === skillPickerIndex ? 'true' : 'false'"
              @mouseenter="skillPickerIndex = row.index"
              @mousedown.prevent="selectSkill(row.skill)"
              @click="selectSkill(row.skill)"
            >
              <SkillIcon
                :icon="row.skill.icon"
                :category="iconCategoryFor(row.skill.category_id)"
                size="xs"
              />
              <span class="skill-picker-body">
                <span class="skill-picker-name"
                  ><span
                    v-for="(part, pi) in row.nameParts"
                    :key="'n' + pi"
                    :class="{ 'skill-picker-hit': part.hit }"
                    >{{ part.text }}</span
                  ></span
                >
                <span class="skill-picker-desc"
                  ><span
                    v-for="(part, pi) in row.descParts"
                    :key="'d' + pi"
                    :class="{ 'skill-picker-hit': part.hit }"
                    >{{ part.text }}</span
                  ></span
                >
              </span>
              <span class="skill-picker-slash">/{{ row.skill.dir_name }}</span>
            </li>
          </ul>
        </div>
      </div>
    </Teleport>

    <div v-if="showFooter" class="input-footer">
      <span class="footer-text">内容由AI生成，请仔细甄别</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { commandItems } from "../../../shared/compaction/state.js";
import FileReferencePicker from "./FileReferencePicker.vue";
import FileReferenceTag from "./FileReferenceTag.vue";
import {
  addReference,
  restoreReferenceDraft,
} from "../../../shared/fileReferences.js";
import {
  formatCharCount,
  formatContextWindow,
  longMessageFileContent,
  longMessageFileName,
  longMessageNotice,
  needsOffload,
  usagePercent,
} from "../../../shared/chat/longMessage.js";
import {
  uploadFile,
  deleteFile,
  getWorkspaceTree,
} from "../../../api/files.js";
import {
  getKnowledgeStatus,
  getWebSearchStatus,
  getUploadLimits,
} from "../../../api/settings.js";
import { getUserSkills, importSkill } from "../../../api/skills.js";
import { iconCategoryFor } from "../../../utils/skillCategories.js";
import { interactionCommandItems, interactionModeFromTokenMap, modeTokenSkill, withoutInteractionSkills } from "../../../shared/chat/planInteraction.js";

// 镜像层为画出技能芯片的外扩留白，把裁剪盒向外扩了这么多 px（见 .input-mirror 的 padding/margin）：
// 补滚动条占位时要一起加上，否则镜像内容比 textarea 窄、换行位置对不上。
const MIRROR_BLEED = 6;
import {
  buildMessageText,
  parseSlashQuery,
  splitByQuery,
} from "../../../utils/skillSlash.js";
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
} from "../../../utils/skillPillTokens.js";
import { getConnectors, toggleConnector } from "../../../api/connectors.js";
import {
  emitConnectorsChanged,
  onConnectorsChanged,
} from "../../../utils/connectorSync.js";
import { listExperts, listPublicExperts } from "../../../api/expert.js";
import {
  expertAvatarChar,
  expertAvatarClass,
} from "../../../utils/expertAvatar.js";
import { formatSize } from "../../../utils/format.js";
import SkillPicker from "./SkillPicker.vue";
import UploadTray from "./UploadTray.vue";
import {
  useContextDuration,
  useContextRing,
} from "../composables/useContextUsage.js";
import { useFileReferences } from "../composables/useFileReferences.js";
import { usePromptEnhancement } from "../composables/usePromptEnhancement.js";
import ModelPicker from "./ModelPicker.vue";
import PromptEnhancementActions from "./PromptEnhancementActions.vue";
import KnowledgeScopeSelector from "../../knowledge/KnowledgeScopeSelector.vue";
import FileIcon from "../../../shared/components/FileIcon.vue";
import SkillIcon from "../../../shared/components/SkillIcon.vue";

// 输入框最大高度：5 行（行高 1.6 × 15px = 24px）。文本再长就在框内滚动（右侧滚动条），
// 输入框不再无限增高——与 CSS 的 max-height 保持一致，两处改要一起改。
const TEXTAREA_MAX_ROWS = 5;
const TEXTAREA_LINE_HEIGHT = 24;
const TEXTAREA_MAX_HEIGHT = TEXTAREA_MAX_ROWS * TEXTAREA_LINE_HEIGHT;

const props = defineProps({
  compactReason: { type: String, default: "请先开始对话" },
  compactBusy: { type: Boolean, default: false },
  disabled: {
    type: Boolean,
    default: false,
  },
  sessionId: {
    type: String,
    default: null,
  },
  isStreaming: {
    type: Boolean,
    default: false,
  },
  sessionUsage: {
    type: Object,
    default: () => ({
      input_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      context_length: null,
      auto_compress_tokens: null,
      context_tokens: 0,
    }),
  },
  sessionDuration: {
    type: Number,
    default: 0,
  },
  iterationCount: {
    type: Number,
    default: 0,
  },
  models: {
    type: Array,
    default: () => [],
  },
  selectedModel: {
    type: String,
    default: null,
  },
  showFooter: {
    type: Boolean,
    default: false,
  },
  // 当前会话选中的专家 Agent（{export_agent_id, export_name_cn} 或 null）。
  // 非空时输入框工具栏显示专家胶囊，且本会话消息走 /agent/chat/stream（带 expert_agent_id）。
  activeExpert: {
    type: Object,
    default: null,
  },
  // 外部预填的问题（如专家详情弹窗点「专家帮你做」示例）：非空时写入输入框，
  // 写入后立即 emit('prefillApplied') 通知父组件清空，避免重复触发。
  prefillText: {
    type: String,
    default: "",
  },
  // 输入草稿的 sessionStorage key：主页面与 AI 侧边栏各存一份
  draftKey: { type: String, default: "easy_agent_input_draft" },
  // 是否消费技能中心「创建技能」预置的待选技能（只应由主页面输入框消费）
  consumePendingSkill: { type: Boolean, default: true },
  // 「+」二级面板底部的「在管理面板中打开」入口
  showManageEntry: { type: Boolean, default: true },
  // 窄输入框（AI 侧边栏）：工具栏不放专家胶囊（会挤掉发送按钮，已选状态只在「+」→ 专家面板里体现），
  // 专家行改为名称、描述分两行，选中后面板不收起
  compact: { type: Boolean, default: false },
});

const emit = defineEmits([
  "compact-context",
  "send",
  "stop",
  "createSession",
  "update:selectedModel",
  "typing",
  "clearExpert",
  "selectExpert",
  "prefillApplied",
  "openPanel",
  "openKnowledge",
  "webSearchChange",
]);

const message = ref("");
const {
  fileReferences,
  referenceOpen,
  referenceSearch,
  referencePicker,
  updateReference,
  closeReference,
  resetReferenceQuery,
  selectReference,
  removeReference,
  restoreInitialReferences,
} = useFileReferences({
  props,
  message,
  replaceTextWithCaret,
  closeSkillPicker,
  updateSkillPickerPosition,
});
const textareaRef = ref(null);
const uploadedFiles = ref([]);
// 长文本转附件：保存中禁止重复点击；用户可关闭提示，正文回落到安全范围时重新可提示
const offloadingLongMessage = ref(false);
const longMessageDismissed = ref(false);
const longMessageChars = computed(() => message.value.trim().length);
const longMessageWords = computed(() =>
  formatCharCount(longMessageChars.value),
);
// 预算按当前模型的真实窗口算：会话用量（含 system prompt / 技能 / 历史）优先，
// 还没跑过任何一轮时退回选中模型的 context_length，再退回 131072
const longMessageUsage = computed(() => ({
  contextLength:
    props.sessionUsage?.context_length ||
    (props.models || []).find(
      m =>
        m &&
        (m.name === props.selectedModel || m.model === props.selectedModel),
    )?.context_length ||
    null,
  usedTokens: props.sessionUsage?.context_tokens || 0,
  autoCompressTokens: props.sessionUsage?.auto_compress_tokens || null,
}));
const longMessageWindow = computed(() =>
  formatContextWindow(longMessageUsage.value.contextLength),
);
const longMessageUsagePercent = computed(() =>
  usagePercent(longMessageUsage.value),
);
const longMessageHint = computed(
  () =>
    !longMessageDismissed.value &&
    needsOffload(message.value),
);
watch(longMessageChars, () => {
  if (!needsOffload(message.value))
    longMessageDismissed.value = false;
});
// 新对话里勾选的知识库：只暂存在前端，首条消息发送时随请求提交，
// 避免“选知识库”阶段提前创建空会话（已有会话仍走 knowledge-scope 接口保存）。
const pendingKnowledgeBaseIds = ref([]);
// 发送前正在把“待发送”附件上传到刚创建/已有会话，防止重复点击发送
const preparingFiles = ref(false);

// 并行上传时共享的「等待会话创建」Promise（避免重复触发 createSession）
let pendingSessionPromise = null;

// ── 上传限额（后端 config.yaml 的 upload_limits 段，经 /agent/settings/upload-limits 下发）──
// 约束「单个会话、单次对话」的附件：即输入框待发送列表，发送后清空即重置额度。
// 拉取失败时保留这里的默认值（10MB / 5 个 / 20MB），绝不因接口异常而放行。
const DEFAULT_UPLOAD_LIMITS = {
  enabled: true,
  maxFileSizeMb: 10,
  maxFiles: 5,
  maxTotalMb: 20,
};
const uploadLimits = ref({ ...DEFAULT_UPLOAD_LIMITS });
// 汇总提示条（单条文案，2.6s 自动消失）
const uploadNotice = ref("");
let uploadNoticeTimer = null;

function mbToBytes(mb) {
  return (Number(mb) || 0) * 1024 * 1024;
}

// 计额口径：上传失败的文件不占额度（上传中/已上传都占）
const chargedFiles = computed(() =>
  uploadedFiles.value.filter(f => f.uploadStatus !== "error"),
);
const usedCount = computed(() => chargedFiles.value.length);
const usedBytes = computed(() =>
  chargedFiles.value.reduce((sum, f) => sum + (f.size || 0), 0),
);
const failedCount = computed(
  () => uploadedFiles.value.filter(f => f.uploadStatus === "error").length,
);
const remainBytes = computed(() =>
  Math.max(mbToBytes(uploadLimits.value.maxTotalMb) - usedBytes.value, 0),
);
const limitsText = computed(() => {
  const L = uploadLimits.value;
  if (!L.enabled) return "";
  const failedTip =
    failedCount.value > 0 ? `（${failedCount.value} 个失败未计入）` : "";
  return `已用 ${formatSize(usedBytes.value)} / ${L.maxTotalMb}MB · ${usedCount.value} / ${L.maxFiles} 个${failedTip}`;
});
const limitsFull = computed(() => {
  const L = uploadLimits.value;
  return L.enabled && usedCount.value >= L.maxFiles;
});

function showUploadNotice(text) {
  if (!text) return;
  uploadNotice.value = text;
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer);
  uploadNoticeTimer = setTimeout(() => {
    uploadNotice.value = "";
  }, 2600);
}

function dismissUploadNotice() {
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer);
  uploadNotice.value = "";
}

async function loadUploadLimits() {
  try {
    const data = await getUploadLimits();
    const toPositive = (v, fallback) => (Number(v) > 0 ? Number(v) : fallback);
    uploadLimits.value = {
      enabled: data.enabled !== false,
      maxFileSizeMb: toPositive(
        data.max_file_size_mb,
        DEFAULT_UPLOAD_LIMITS.maxFileSizeMb,
      ),
      maxFiles: toPositive(
        data.max_files_per_message,
        DEFAULT_UPLOAD_LIMITS.maxFiles,
      ),
      maxTotalMb: toPositive(
        data.max_total_size_mb,
        DEFAULT_UPLOAD_LIMITS.maxTotalMb,
      ),
    };
  } catch (e) {
    console.error("获取上传限额失败，使用默认限额（10MB / 5 个 / 20MB）:", e);
  }
}

/**
 * 选出本批可以上传的文件（入列前校验，避免"传了一半才发现超限"）。
 *
 * 规则：
 * - 先做**重复过滤**：文件名 + 大小 + 修改时间都相同视为同一文件，
 *   与本轮已添加的文件（含本批内互相重复）只保留一个，其余忽略；
 *   上传失败（error）的条目不算已添加，允许重新选择同一个文件再传；
 *   uploadedFiles 在发送后清空，因此去重只作用于本轮，不影响其它轮次；
 * - enabled=false 时不限额度；
 * - 数量不满足（已有非失败数 + 去重后本批数 > 上限）→ **整批拒绝**，让用户重新选择；
 * - 逐个文件检查单文件大小与总大小 → **只跳过超限的那一个**，其余照常上传；
 * - 失败文件不占额度，因此删除/失败都会立即释放名额与空间。
 */
function uploadFileKey(file) {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified ?? ""}`;
}

function pickAcceptable(files) {
  const L = uploadLimits.value;

  // 重复过滤：与本轮已添加文件重复、或本批内互相重复的，只保留第一个
  const existingKeys = new Set(
    uploadedFiles.value
      .filter(f => f.uploadStatus !== "error")
      .map(
        f => `${f.filename}\u0000${f.size}\u0000${f.file?.lastModified ?? ""}`,
      ),
  );
  const deduped = [];
  const rejected = [];
  const batchKeys = new Set();
  for (const file of files) {
    const key = uploadFileKey(file);
    if (existingKeys.has(key) || batchKeys.has(key)) {
      rejected.push({ file, reason: "duplicate" });
      continue;
    }
    batchKeys.add(key);
    deduped.push(file);
  }

  if (!L.enabled) return { accepted: deduped, rejected, batchRejected: false };

  let count = usedCount.value;
  let total = usedBytes.value;
  const accepted = [];

  if (count + deduped.length > L.maxFiles) {
    return {
      accepted: [],
      rejected,
      batchRejected: true,
      message: `最多只能上传 ${L.maxFiles} 个文件（已用 ${count} 个），请重新选择`,
    };
  }

  for (const file of deduped) {
    if (file.size > mbToBytes(L.maxFileSizeMb)) {
      rejected.push({ file, reason: "file_size" });
      continue;
    }
    if (total + file.size > mbToBytes(L.maxTotalMb)) {
      rejected.push({ file, reason: "total" });
      continue;
    }
    accepted.push(file);
    count += 1;
    total += file.size;
  }
  return { accepted, rejected, batchRejected: false };
}

/** 把被拒文件拼成一条汇总提示 */
function buildRejectNotice(result, acceptedCount) {
  const L = uploadLimits.value;
  const parts = result.rejected.map(({ file, reason }) => {
    if (reason === "duplicate") {
      return `${file.name}（与本轮已添加文件重复，已过滤）`;
    }
    if (reason === "file_size") {
      return `${file.name}（超过单文件上限 ${L.maxFileSizeMb}MB）`;
    }
    if (reason === "total") {
      return `${file.name}（超出剩余空间 ${formatSize(remainBytes.value)}，总上限 ${L.maxTotalMb}MB）`;
    }
    return file.name;
  });
  const onlyDuplicates =
    result.rejected.length > 0 &&
    result.rejected.every(r => r.reason === "duplicate");
  const head =
    acceptedCount > 0
      ? `已添加 ${acceptedCount} 个文件，忽略 ${result.rejected.length} 个：`
      : onlyDuplicates
        ? `已忽略 ${result.rejected.length} 个重复文件：`
        : `无法上传，已忽略 ${result.rejected.length} 个文件：`;
  return head + parts.join("；");
}

// 外部预填问题（专家详情弹窗的示例提示词）：写入输入框并聚焦，随后通知父组件清空。
// immediate: 必须开启 —— 详情弹窗在 SkillCenter 面板里，点示例问题会关闭面板并让
// Chat/ChatInput 重新挂载，此时 prefillText 初始值即非空；没有 immediate 的 watch
// 只响应"变化"，挂载时的非空初始值会被整个丢掉，回填失效。
watch(
  () => props.prefillText,
  val => {
    if (!val) return;
    message.value = val;
    nextTick(() => {
      autoResize();
      textareaRef.value?.focus();
    });
    emit("prefillApplied");
  },
  { immediate: true },
);

// ========== 技能选择（输入 / 唤起） ==========
// 已选技能不是独立数组，而是**文本里的内联标签**：token 形如 `/显示名`（见 utils/skillPillTokens.js）。
// 标签的可见文字与 textarea 文本逐字相同，所以光标位置、Backspace 判定都是纯文本下标运算，
// 桌面端 / 移动端 / 连续选多个技能的行为完全一致；选中状态由文本推导，不会与文本脱节。
// 只有启用状态的技能会被 Agent 加载，停用技能即使手打 /name 也不生效，故选择面板只列启用的。
const knownSkills = ref([]);
const tokenMap = computed(() => buildTokenMap(knownSkills.value));

/** 记住技能元信息（标签 `/显示名` 要靠它反解技能）：/ 面板与「+」面板的接口都会往这里补 */
function rememberSkills(skills) {
  const incoming = (Array.isArray(skills) ? skills : []).filter(
    s => s && s.dir_name,
  );
  if (!incoming.length) return;
  const byDir = new Map(knownSkills.value.map(s => [s.dir_name, s]));
  let changed = false;
  for (const skill of incoming) {
    const prev = byDir.get(skill.dir_name);
    if (!prev) {
      byDir.set(skill.dir_name, skill);
      changed = true;
    } else if ((prev.name || "") !== (skill.name || "")) {
      // 技能改名后，标签要按最新显示名解析
      byDir.set(skill.dir_name, { ...prev, ...skill });
      changed = true;
    }
  }
  if (changed) knownSkills.value = Array.from(byDir.values());
}
// 连接器的启用状态改由「+」→ 连接器面板里的开关控制（与「专家·技能·连接器」面板同一份状态），
// 不再点行插入输入框、也不再随每轮消息提交。

// ========== 镜像渲染层（内联技能标签） ==========
// textarea 的文字设为透明，用户看到的文字/标签都来自 .input-mirror 镜像层：
// 标签是文本里的一段子串，这里按 token 把文本分段渲染（文字段 + 标签段），
// 所以标签天然落在它在文本里的位置（第一行、句中、行尾都行）。
// 镜像层必须与 textarea 逐像素同宽同字体，换行位置才会一致 —— 见 .input-mirror 的 CSS 注释。
const mirrorRef = ref(null);
const mirrorText = ref("");
// 上一次 textarea 的文本：移动端兜底自愈要拿它做差分（见 onInput）
let lastInputText = "";

/**
 * 把 textarea 的内容/滚动/滚动条占位同步给镜像层。
 * 输入（含输入法合成中）、程序化赋值、滚动后都要调，否则两侧文字会对不上。
 */
function syncMirror() {
  const ta = textareaRef.value;
  const mirror = mirrorRef.value;
  if (!ta || !mirror) return;
  // 直接读 textarea：输入法合成中的文字 v-model 还没提交，镜像也要实时显示
  mirrorText.value = ta.value;
  lastInputText = ta.value;
  // 出现纵向滚动条后 textarea 内容区变窄，镜像补同等右内边距，保证两侧换行位置一致
  // （镜像自身还有 MIRROR_BLEED 的 padding 用于扩裁剪盒，要一起算进去，否则内容窄 6px 导致换行错位）
  const gutter = ta.offsetWidth - ta.clientWidth;
  mirror.style.paddingRight = gutter > 0 ? `${gutter + MIRROR_BLEED}px` : "";
  // 不加偏移：镜像的 padding-top 与负 margin-top 相互抵消，scrollTop 与 textarea 保持同值即可对齐
  mirror.scrollTop = ta.scrollTop;
}

const skillPickerOpen = ref(false);
const skillPickerIndex = ref(0);
const skillPickerLoading = ref(false);
const skillOptions = ref([]);
const skillQuery = ref("");
const slashStart = ref(-1);
const inputBoxRef = ref(null);
const skillPickerRef = ref(null);
const skillPickerStyle = ref({});
let skillLoadedAt = 0;

// 镜像层的分段结果：普通文字段 + 技能标签段（标签文字与 textarea 文本逐字相同）
const mirrorSegments = computed(() =>
  buildSegments(
    mirrorText.value,
    tokenMap.value,
    // 正在输入 `/查询词` 检索时，半截查询词不要立刻渲染成标签（否则边打字边闪）
    skillPickerOpen.value ? { suppressStart: slashStart.value } : null,
  ),
);
// 已选技能目录名（按出现顺序去重）：canSend 与发送时的 skills 字段。
// = 输入框内联标签 + 预选技能（preselectedSkillDirs，见 restorePendingSkill）：
// 外部入口（如专家面板「新建/修改专家」）在草稿里用自然语言点名技能，
// 不插内联标签、只把技能记为已选，发送时随 skills 字段交给后端激活。
const preselectedSkillDirs = ref(new Set());

// 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：发送时随请求的
// pending_expert_id 字段上行，后端校验归属后绑定为 save_expert 的修改目标。
const pendingExpertEditId = ref("");

const {
  enhancement,
  canEnhance,
  invalidateEnhancement,
  enhancePrompt,
  enhancementCompositionStart,
  enhancementCompositionEnd,
} = usePromptEnhancement({
  props,
  message,
  fileReferences,
  tokenMap,
  knownSkills,
  preselectedSkillDirs,
  pendingExpertEditId,
  preparingFiles,
  textareaRef,
  autoResize,
  syncMirror,
});

const selectedSkillDirs = computed(() => {
  const dirs = new Set(preselectedSkillDirs.value);
  for (const dir of skillDirsInText(message.value, tokenMap.value))
    dirs.add(dir);
  return withoutInteractionSkills(Array.from(dirs), tokenMap.value);
});

const interactionMode = computed(() =>
  interactionModeFromTokenMap(
    tokenMap.value,
    skillDirsInText(message.value, tokenMap.value),
  ),
);

/**
 * 技能检索：优先按「目录名 / 技能名」匹配；仅当没有任何名称命中时，才回退到描述匹配。
 * 描述是一大段英文说明，几乎每个字母都能命中（如 m → manipulate / from / time），
 * 直接参与匹配会让检索看起来「完全没生效」，因此降级为兜底。
 */
const filteredSkills = computed(() => {
  const q = skillQuery.value.trim().toLowerCase();
  if (!q) return skillOptions.value;
  const byName = skillOptions.value.filter(
    s =>
      (s.dir_name || "").toLowerCase().includes(q) ||
      (s.name || "").toLowerCase().includes(q),
  );
  if (byName.length > 0) return byName;
  return skillOptions.value.filter(s =>
    (s.description || "").toLowerCase().includes(q),
  );
});

// 按类型纵向分组，键盘索引仅包含可选择的行。
const menuItems = computed(() => [
  ...interactionCommandItems(skillQuery.value),
  ...commandItems(skillQuery.value, props.compactReason, props.compactBusy),
  ...filteredSkills.value,
]);
const skillRows = computed(() =>
  menuItems.value.map((skill, index) => ({
    skill,
    index,
    nameParts: splitByQuery(skill.name || skill.dir_name, skillQuery.value),
    descParts: splitByQuery(skill.description || "暂无描述", skillQuery.value),
  })),
);

const pickerGroups = computed(() =>
  [
    { title: "指令", rows: skillRows.value.filter(row => row.skill.command) },
    { title: "技能", rows: skillRows.value.filter(row => !row.skill.command) },
  ].filter(group => group.rows.length),
);

async function loadSkillOptions() {
  const now = Date.now();
  // 技能列表变动不频繁，60s 内复用上次结果，避免每次输入 / 都打接口
  if (skillOptions.value.length && now - skillLoadedAt < 60000) return;
  skillPickerLoading.value = true;
  try {
    const data = await getUserSkills();
    // 标签解析要认识所有技能（草稿里可能留着已停用技能的标签），故先登记全量再过滤面板列表
    rememberSkills(data.skills || []);
    skillOptions.value = (data.skills || []).filter(s => s.enabled);
    skillLoadedAt = now;
  } catch (e) {
    console.error("获取技能列表失败:", e);
  } finally {
    skillPickerLoading.value = false;
  }
}

/** 面板出现在输入框上方（与模型下拉一致的 Teleport + fixed 定位方式） */
function updateSkillPickerPosition() {
  const box = inputBoxRef.value;
  if (!box) return;
  const rect = box.getBoundingClientRect();
  const margin = 8;
  const maxPanelHeight = 320;
  const spaceAbove = rect.top - margin * 2;
  const spaceBelow = window.innerHeight - rect.bottom - margin * 2;
  // 技能装多之后面板会撑到最大高度：若仍按「贴着输入框上沿 + 固定高度」定位，
  // 面板顶部会越过视口，首个技能被裁在屏幕外且怎么滚都够不着。因此改为优先向上
  // 弹出、但把高度限制在输入框上方的可用空间内（下方空间明显更宽裕时才向下弹）。
  const placeAbove = spaceAbove >= 160 || spaceAbove >= spaceBelow;
  const available = placeAbove ? spaceAbove : spaceBelow;
  const maxHeight = Math.max(96, Math.min(maxPanelHeight, available));
  skillPickerStyle.value = {
    position: "fixed",
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    maxHeight: `${Math.round(maxHeight)}px`,
    ...(placeAbove
      ? { bottom: `${window.innerHeight - rect.top + margin}px` }
      : { top: `${rect.bottom + margin}px` }),
  };
}

/** 键盘切换选项时把当前高亮项滚进可视区（只滚列表本身，不带动外层级） */
function scrollActiveSkillIntoView() {
  const list = skillPickerRef.value?.querySelector(".skill-picker-list");
  const item =
    list?.querySelectorAll(".skill-picker-item")[skillPickerIndex.value];
  if (!list || !item) return;
  const top = item.offsetTop;
  const bottom = top + item.offsetHeight;
  if (top < list.scrollTop) list.scrollTop = top;
  else if (bottom > list.scrollTop + list.clientHeight)
    list.scrollTop = bottom - list.clientHeight;
}

/** 判定光标前是否为「/查询词」（/ 位于开头或空白之后），决定展开或收起面板 */
function updateSkillPicker() {
  const ta = textareaRef.value;
  if (!ta) return;
  const caret = ta.selectionStart ?? ta.value.length;
  const slash = parseSlashQuery(ta.value, caret);
  if (!slash) {
    closeSkillPicker();
    return;
  }

  slashStart.value = slash.start;
  skillQuery.value = slash.query;
  skillPickerIndex.value = 0;
  skillPickerOpen.value = true;
  updateSkillPickerPosition();
  loadSkillOptions();
}

function closeSkillPicker() {
  skillPickerOpen.value = false;
  skillPickerIndex.value = 0;
  skillQuery.value = "";
  slashStart.value = -1;
}

/**
 * 选中技能：删掉刚输入的 /查询词，在光标处插入内联标签 `/显示名`，光标停在标签之后。
 * 已在文本里的同一技能不重复插入（光标落到已有标签之后）。
 */
async function selectSkill(skill) {
  if (!skillPickerOpen.value || !skill || !skill.dir_name) return;

  const ta = textareaRef.value;
  const base = ta?.value ?? message.value;
  const caret = ta?.selectionStart ?? base.length;
  // 以当前光标为准重算一次 /查询词 的范围，避免用户中途改动文本后误删内容
  const slash = parseSlashQuery(base, caret);
  const start = slash ? slash.start : caret;
  const cleared = base.slice(0, start) + base.slice(caret);

  if (skill.command) {
    if (skill.interactionMode) {
      if (isStreaming.value || props.disabled) return;
      const modeSkill = modeTokenSkill(skill);
      rememberSkills([modeSkill]);
      const existing = skillSegments(cleared, tokenMap.value).find(
        seg => seg.dirName === modeSkill.dir_name,
      );
      const next = existing
        ? { text: cleared, caret: existing.end }
        : insertSkillToken(cleared, start, tokenForSkill(modeSkill));
      closeSkillPicker();
      await replaceTextWithCaret(next);
      return;
    }
    if (props.compactReason || skill.disabled) return;
    closeSkillPicker();
    replaceTextWithCaret({ text: cleared, caret: start });
    emit("compact-context");
    return;
  }

  rememberSkills([skill]);
  const existing = skillSegments(cleared, tokenMap.value).find(
    seg => seg.dirName === skill.dir_name,
  );
  const next = existing
    ? { text: cleared, caret: existing.end }
    : insertSkillToken(cleared, start, tokenForSkill(skill));

  closeSkillPicker();
  await replaceTextWithCaret(next);
  console.log(`[${new Date().toISOString()}] [技能选择] ${skill.dir_name}`);
}

/**
 * 写入新文本并把光标放回指定下标（标签插入/删除后的统一收尾）。
 * 等 DOM 更新后再设置光标，避免 v-model 回写把光标顶到末尾；焦点始终留在输入框。
 */
function replaceTextWithCaret(next) {
  const ta = textareaRef.value;
  message.value = next.text;
  return nextTick(() => {
    // v-model 可能在本轮 input 里把旧值又写回 message（输入事件的处理顺序由框架决定），
    // 这里再断言一次，保证 ref 与 DOM 都是我们算好的文本，否则后续发送会带上半个标签。
    if (message.value !== next.text) message.value = next.text;
    autoResize();
    if (ta) {
      if (ta.value !== next.text) ta.value = next.text;
      ta.focus();
      ta.setSelectionRange(next.caret, next.caret);
    }
    updateCaretLine();
    syncMirror();
  });
}

/** 整段删除技能标签 + 复位光标（Backspace / Delete / 点击标签都走这里） */
function applyTokenRemoval(base, segsToRemove) {
  if (!Array.isArray(segsToRemove) || !segsToRemove.length) return;
  replaceTextWithCaret(removeTokenSegments(base, segsToRemove));
}

/** 点击标签 / 面板里取消选择：把该技能的标签从文本里整段删掉 */
function removeSkillToken(dirName) {
  const ta = textareaRef.value;
  const base = ta?.value ?? message.value;
  const segs = skillSegments(base, tokenMap.value).filter(
    seg => seg.dirName === dirName,
  );
  if (!segs.length) return;
  applyTokenRemoval(base, segs);
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
  let raw = null;
  try {
    raw = sessionStorage.getItem(PENDING_SKILL_KEY);
    if (raw) sessionStorage.removeItem(PENDING_SKILL_KEY);
  } catch (_) {
    /* 隐私模式等场景忽略 */
  }
  if (!raw) return;

  try {
    const parsed = JSON.parse(raw);
    // 两种载荷：单个技能对象（技能中心）与 {skills: [...]}（专家面板：
    // 新建带 expert-creator，编辑还会带上该专家已绑定的技能包）
    const list = Array.isArray(parsed?.skills)
      ? parsed.skills
      : parsed
        ? [parsed]
        : [];
    for (const skill of list) {
      if (!skill?.dir_name) continue;
      if (skill.command) {
        if (props.compactReason || skill.disabled) return;
        closeSkillPicker();
        replaceTextWithCaret({ text: cleared, caret: start });
        emit("compact-context");
        return;
      }

      rememberSkills([skill]);
      if (skill.insert_token === false) {
        preselectedSkillDirs.value.add(skill.dir_name);
        continue;
      }
      const ta = textareaRef.value;
      const base = ta?.value ?? message.value;
      if (
        skillSegments(base, tokenMap.value).some(
          seg => seg.dirName === skill.dir_name,
        )
      )
        continue;
      replaceTextWithCaret(
        insertSkillToken(base, base.length, tokenForSkill(skill)),
      );
    }
  } catch (e) {
    console.error("恢复待选技能失败:", e);
  }
}

/**
 * Backspace / Delete 紧邻标签时整段删除标签（光标在别处则保持常规的逐字符删除）。
 * 光标「紧邻标签之后」= selectionStart === 标签结束下标，与文本是同一套下标，桌面端/移动端一致。
 */
function handleTokenDeleteKey(event) {
  if (event.isComposing) return false;
  if (event.key !== "Backspace" && event.key !== "Delete") return false;
  const ta = event.target;
  if (!ta || typeof ta.selectionStart !== "number") return false;
  // 有选区时按常规删除（用户是在删一段选中的文本）
  if (ta.selectionStart !== ta.selectionEnd) return false;
  const caret = ta.selectionStart;
  const seg =
    event.key === "Backspace"
      ? tokenBeforeCaret(ta.value, caret, tokenMap.value)
      : tokenAfterCaret(ta.value, caret, tokenMap.value);
  if (!seg) return false;
  event.preventDefault();
  applyTokenRemoval(ta.value, [seg]);
  return true;
}

/**
 * beforeinput 路径：移动端（Android Chrome）Backspace 不给 keydown，键盘删除只能在这里拦。
 * 拦截后自行整段删标签；拦不到的情况由 onInput 的差分自愈兜底（见 reconcileTokenDeletion）。
 */
function onBeforeInput(event) {
  invalidateEnhancement();
  if (event.isComposing) return;
  const deleteBackward = event.inputType === "deleteContentBackward";
  const deleteForward = event.inputType === "deleteContentForward";
  if (!deleteBackward && !deleteForward) return;
  const ta = event.target;
  if (!ta || typeof ta.selectionStart !== "number") return;
  if (ta.selectionStart !== ta.selectionEnd) return;
  const caret = ta.selectionStart;
  const seg = deleteBackward
    ? tokenBeforeCaret(ta.value, caret, tokenMap.value)
    : tokenAfterCaret(ta.value, caret, tokenMap.value);
  if (!seg) return;
  event.preventDefault();
  applyTokenRemoval(ta.value, [seg]);
}

/** 键盘：面板打开时接管 ↑↓ / Enter / Esc，其余情况保持原有 Enter 发送 */
function onKeydown(event) {
  if (event.isComposing || event.keyCode === 229) return;
  if (referenceOpen.value && referencePicker.value?.key(event)) return;
  if (handleTokenDeleteKey(event)) return;

  if (skillPickerOpen.value) {
    const count = menuItems.value.length;
    if (event.key === "ArrowDown" && count > 0) {
      event.preventDefault();
      skillPickerIndex.value = (skillPickerIndex.value + 1) % count;
      nextTick(scrollActiveSkillIntoView);
      return;
    }
    if (event.key === "ArrowUp" && count > 0) {
      event.preventDefault();
      skillPickerIndex.value = (skillPickerIndex.value - 1 + count) % count;
      nextTick(scrollActiveSkillIntoView);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeSkillPicker();
      return;
    }
    if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") {
      const skill = menuItems.value[skillPickerIndex.value];
      if (skill) {
        event.preventDefault();
        selectSkill(skill);
        return;
      }
      closeSkillPicker();
    }
  }

  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    !event.isComposing
  ) {
    event.preventDefault();
    send();
  }
}

/** 点击面板外的区域时收起技能面板 */
function closeSkillPickerOnClick(event) {
  if (referenceOpen.value) {
    if (referencePicker.value?.$el.contains(event.target)) return;
    if (event.target !== textareaRef.value) closeReference();
  }
  if (!skillPickerOpen.value) return;
  const picker = skillPickerRef.value;
  if (picker && picker.contains(event.target)) return;
  closeSkillPicker();
}

/** 窗口尺寸变化时重新定位（移动端键盘弹出会触发 resize） */
function handleViewportChange() {
  if (skillPickerOpen.value || referenceOpen.value) updateSkillPickerPosition();
}

// ========== 联网搜索 ==========
// 开关状态仅本次页面会话有效（默认关闭）：开启后本轮消息携带 enable_web_search，
// 后端注入 web_search 工具并追加联网搜索行为约束。
const knowledgeEnabled = ref(false);
const webSearchEnabled = ref(false);
const webSearchStatus = ref({ enabled: false, configured: false });
const webSearchUnavailable = computed(() => !webSearchStatus.value.configured);
const webSearchTitle = computed(() => {
  if (!webSearchStatus.value.configured) {
    return "联网搜索未配置（需配置 WEB_SEARCH_API_KEY 后重启后端）";
  }
  return webSearchEnabled.value
    ? "联网搜索已开启，点击关闭"
    : "联网搜索（点击开启）";
});

function toggleWebSearch() {
  if (webSearchUnavailable.value || props.isStreaming || props.disabled) return;
  webSearchEnabled.value = !webSearchEnabled.value;
  console.log(
    `[${new Date().toISOString()}] [联网搜索] ${webSearchEnabled.value ? "已开启" : "已关闭"}`,
  );
}

async function loadKnowledgeStatus() {
  try {
    const data = await getKnowledgeStatus();
    knowledgeEnabled.value = Boolean(data && data.enabled);
  } catch (e) {
    knowledgeEnabled.value = false;
  }
}

async function loadWebSearchStatus() {
  try {
    const data = await getWebSearchStatus();
    webSearchStatus.value = {
      enabled: !!data.enabled,
      configured: !!data.configured,
    };
    if (!data.configured) webSearchEnabled.value = false;
  } catch (e) {
    // 状态获取失败按「不可用」处理，避免用户开启后静默不生效
    console.error("获取联网搜索状态失败:", e);
  }
}

// 待选技能：由技能中心「创建技能」等入口写入，本组件挂载时恢复成技能标签
const PENDING_SKILL_KEY = "easy_agent_pending_skill";

// 待修改专家：由专家面板「编辑」写入（{expert_agent_id, export_name_cn}）。
// 专家 ID 不进消息正文（防篡改），挂载时消费为组件状态，发送时随请求的
// pending_expert_id 字段上行；发送后清空。与后端 save_expert 的 edit_targets 绑定配套。
const PENDING_EXPERT_EDIT_KEY = "easy_agent_pending_expert_edit";

// 光标所在行高亮（overlay 技术：textareal 本身无法按行上色）
const caretLineTop = ref(null); // 高亮条 top，null 表示隐藏
const lineHeight = ref(24); // 行高（px）

function updateCaretLine() {
  syncMirror(); // 聚焦/点击/按键/滚动都会走到这里，顺带把镜像层对齐（含 scrollTop）
  const ta = textareaRef.value;
  if (!ta) return;
  const lh = parseFloat(getComputedStyle(ta).lineHeight);
  if (!isNaN(lh) && lh > 0) lineHeight.value = lh;
  const pos = ta.selectionStart ?? 0;
  const lineIndex = ta.value.slice(0, pos).split("\n").length - 1;
  caretLineTop.value = lineIndex * lineHeight.value - ta.scrollTop;
}

function hideCaretLine() {
  caretLineTop.value = null;
}

// 模型切换：下拉交互在 ModelPicker 组件内，这里只负责向上抛事件
function onModelChange(name) {
  emit("update:selectedModel", name);
  console.log(`[${new Date().toISOString()}] [模型选择] 切换为: ${name}`);
}

// ========== 「+」添加菜单 ==========
const fileInputRef = ref(null);
const showPlusMenu = ref(false);
const plusMenuRef = ref(null);
// 菜单本身（Teleport 到 body）：悬停「知识库」时要用它找菜单项定位
const plusMenuListRef = ref(null);
const plusMenuStyle = ref({});
const knowledgeScope = ref(null);

function togglePlusMenu() {
  if (props.isStreaming || props.disabled) return;
  if (showPlusMenu.value) {
    showPlusMenu.value = false;
    subpanelCategory.value = null;
    knowledgeScope.value?.closePopover();
    cancelPlusClose();
    return;
  }
  // 菜单贴着按钮上方弹出（与模型下拉同款定位方式）
  if (plusMenuRef.value) {
    const rect = plusMenuRef.value.getBoundingClientRect();
    plusMenuStyle.value = {
      position: "fixed",
      bottom: `${window.innerHeight - rect.top + 6}px`,
      left: `${rect.left}px`,
      minWidth: "188px",
    };
  }
  // 只弹出一级菜单：二级面板在悬停 / 点击对应菜单项时才展开
  subpanelCategory.value = null;
  showPlusMenu.value = true;
}

function closePlusMenu(event) {
  // 点击知识库面板内部不算「菜单外」（面板不在菜单 DOM 内，但属于同一浮层交互）
  const scopeRoot = knowledgeScope.value?.$el;
  const inScope = scopeRoot && scopeRoot.contains(event.target);
  if (
    showPlusMenu.value &&
    plusMenuRef.value &&
    !plusMenuRef.value.contains(event.target) &&
    !inScope
  ) {
    showPlusMenu.value = false;
    subpanelCategory.value = null;
    knowledgeScope.value?.closePopover();
  }
  // 技能面板内部点击已被 @click.stop 拦截，能到这里说明点在面板外
  if (
    subpanelCategory.value &&
    plusMenuRef.value &&
    !plusMenuRef.value.contains(event.target)
  ) {
    subpanelCategory.value = null;
  }
}

function handleMenuAddFile() {
  showPlusMenu.value = false;
  subpanelCategory.value = null;
  knowledgeScope.value?.closePopover();
  fileInputRef.value?.click();
}

// 加号菜单「知识库」：点击保持主菜单常驻，面板贴菜单项右侧弹出
function openKnowledgeScope(event) {
  cancelPlusClose();
  subpanelCategory.value = null;
  knowledgeScope.value?.openPopover(event?.currentTarget);
}

// 知识库子面板底部入口：关闭菜单并打开知识库工作台
function openKnowledgeManage() {
  showPlusMenu.value = false;
  subpanelCategory.value = null;
  knowledgeScope.value?.closePopover();
  emit("openKnowledge");
}

// 知识库勾选变化：已有会话由选择器立即 PUT 落库；新对话只暂存，
// 等首条消息发送时随 /agent/chat/stream 提交，由后端建会话后落库。
function handleKnowledgeScopeChange(baseIds) {
  pendingKnowledgeBaseIds.value = props.sessionId ? [] : [...baseIds];
}

// ========== 「+」二级列表：展示当前用户自己的条目（不跳转页面） ==========
// 数据来源均为当前用户：
//   技能   → GET /agent/skill-center/user-skills（workspace/{user}/skills，含已停用）
//   连接器 → GET /agent/connectors（用户 mcp.json 中已安装的内置连接器 + 自定义服务）
//   专家   → 我的专家（/agent/list，仅取可用状态） + 公共专家（/agent/public-list）
// 「+」子面板尺寸与贴边间距：JS 定位与 CSS 宽度必须同源，否则放大后会溢出视口。
// 单行布局（名称 + 描述同排）需要更宽的面板，400px 下描述只剩 130px 左右不够看。
const SUBPANEL_WIDTH = 480;
// 子面板与主菜单之间的水平间隙；CSS 里的 ::before 热桥按「间隙 + 1px 重叠」覆盖这段空白
const SUBPANEL_GAP = 6;
// 子面板高度上限 560px（约 10 行列表）：底边贴主菜单底边、向上生长，避免顶到屏幕顶端。
const SUBPANEL_MAX_HEIGHT = 560;

// 「+」→ 专家：上区「我的专家」走服务端分页（后端 /agent/list 支持 keyword/status/page），
// 一页 5 条，首屏取第 1 页，之后点「点击加载更多」再取下一页。
// 不再用「一次拉全量 + 本地 slice」：库里第 N 页的专家从来没被请求过，关键字再准也搜不到。
const PLUS_EXPERTS_PAGE_SIZE = 5;
// 下区「公共专家」是随发行包内置的目录（只有个位数），继续本地过滤 + 本地分页。
const PUBLIC_EXPERTS_PAGE_SIZE = 6;
const PUBLIC_EXPERTS_MORE_STEP = 3;
// 专家搜索防抖：连续输入时只在停手后打一次接口
const EXPERT_SEARCH_DEBOUNCE_MS = 300;

const subpanelCategory = ref(null); // 当前展开的二级面板：'skills' | 'experts' | 'connectors' | null
const plusListCategory = ref("");
const plusList = ref([]);
const plusListLoading = ref(false);
const plusListError = ref("");
// 专家快捷面板：上区我的专家（服务端分页 + 关键字） / 下区公共（预置）专家（本地过滤 + 分页）
const myExperts = ref([]);
const myExpertsTotal = ref(0);
const myExpertsPage = ref(0);
const myExpertsLoading = ref(false);
const myExpertsError = ref("");
const publicExperts = ref([]);
// 公共专家首屏 6 行，点「还有 N 位」再放 3 行（目录全量下发，只在本地切）
const publicExpertsLimit = ref(PUBLIC_EXPERTS_PAGE_SIZE);
// 上一次真正提交给接口的关键字：面板打开时清空搜索词不该再触发一次「按关键字重查」
const expertsKeyword = ref("");
// 请求序号：快速连续输入会有多个请求在飞，只认最后一次的结果
let expertsRequestSeq = 0;
let expertsSearchTimer = null;
// 子面板搜索（专家 / 技能 / 连接器共用一个关键词，切换分类时清空）
const subpanelKeyword = ref("");

const PLUS_LIST_META = {
  experts: { title: "专家", empty: "暂无专家，敬请期待" },
  skills: {
    title: "我的技能",
    empty: "还没有自己的技能，可在「专家·技能·连接器」面板添加",
  },
  connectors: {
    title: "我的连接器",
    empty: "还没有已添加的连接器，可在「专家·技能·连接器」面板添加",
  },
};

const plusListTitle = computed(
  () => PLUS_LIST_META[plusListCategory.value]?.title || "",
);
const plusListEmptyText = computed(
  () => PLUS_LIST_META[plusListCategory.value]?.empty || "暂无内容",
);
const plusListSearchPlaceholder = computed(() => {
  const meta = {
    experts: "搜索专家",
    skills: "搜索技能",
    connectors: "搜索连接器",
  };
  return meta[plusListCategory.value] || "搜索";
});

/**
 * 专家搜索：中英文名 / 功能描述。
 *
 * 注意只用于**公共专家**（目录全量下发、在本地过滤）；「我的专家」的关键字已下推
 * 给接口（服务端分页，本地拿不到未加载的条目，过滤会漏）。
 */
function filterExperts(list) {
  const kw = subpanelKeyword.value.trim().toLowerCase();
  if (!kw) return list || [];
  return (list || []).filter(item => {
    if (!item) return false;
    const name = String(item.export_name_cn || "").toLowerCase();
    const nameEn = String(item.export_name_en || "").toLowerCase();
    const desc = String(item.export_description || "").toLowerCase();
    return name.includes(kw) || nameEn.includes(kw) || desc.includes(kw);
  });
}

const filteredPublicExperts = computed(() =>
  filterExperts(publicExperts.value),
);
// 有搜索词时，清空的区连标题一起收起；搜索请求在飞时保留标题，否则它会闪一下再出现
const showMineSection = computed(
  () =>
    !subpanelKeyword.value.trim() ||
    myExperts.value.length > 0 ||
    myExpertsLoading.value,
);
const showPublicSection = computed(
  () => !subpanelKeyword.value.trim() || filteredPublicExperts.value.length > 0,
);

// 还有下一页（我的专家：加载满 total 后按钮消失）
const hasMoreMine = computed(
  () => myExperts.value.length < myExpertsTotal.value,
);

// 公共专家：搜索时全量展示（避免「搜到第 4 条却藏在『还有 N 位』后面」），否则按 limit 分页
const visiblePublicExperts = computed(() =>
  subpanelKeyword.value.trim()
    ? filteredPublicExperts.value
    : filteredPublicExperts.value.slice(0, publicExpertsLimit.value),
);

// 标题右侧计数：专家 = 已加载的我的专家 + 公共专家命中数；技能 = 技能列表过滤后条数
const plusListCount = computed(() => {
  if (plusListCategory.value === "experts") {
    return myExperts.value.length + filteredPublicExperts.value.length;
  }
  if (plusListCategory.value === "skills")
    return filteredSkillList.value.length;
  return filteredPlusList.value.length;
});

// 空列表时不显示搜索框（免得对着空列表以为「搜不到」）
const hasPanelItems = computed(() =>
  plusListCategory.value === "experts"
    ? myExperts.value.length + publicExperts.value.length > 0
    : plusList.value.length > 0,
);

function openPlusList(category) {
  plusListCategory.value = category;
  plusListLoading.value = true;
  plusListError.value = "";
  plusList.value = [];
  // 每次打开都重新拉取（接口很轻，且公共专家目录是服务端实时扫描的）
  myExperts.value = [];
  myExpertsTotal.value = 0;
  myExpertsPage.value = 0;
  myExpertsLoading.value = false;
  myExpertsError.value = "";
  publicExperts.value = [];
  publicExpertsLimit.value = PUBLIC_EXPERTS_PAGE_SIZE;
  // 三个面板共用一份搜索词：切分类时一律清空，避免把上一个面板的关键字带过来
  subpanelKeyword.value = "";
  // 同步记下「已提交的关键字」，让清空动作不触发 subpanelKeyword 的按关键字重查
  expertsKeyword.value = "";
  if (expertsSearchTimer) {
    clearTimeout(expertsSearchTimer);
    expertsSearchTimer = null;
  }
  // 作废上一轮还在飞的请求，避免旧结果落到新面板上
  expertsRequestSeq += 1;
  if (category === "connectors") {
    // 开关状态随连接器目录一起拉取（loadPlusList）
    clearConnectorNotice();
  }
  loadPlusList(category);
}

// ========== 悬停触发二级面板 ==========
// 鼠标悬停主菜单项即切换对应面板（技能=独立子面板，专家/连接器=list 模式），
// 无需点击；点击仍保留（触屏/键盘回退）。鼠标离开「主菜单 + 子面板」整个区域
// 超过 250ms 才整体关闭，留出移入子面板的时间（两者之间有空隙）。
let plusHoverTimer = null;

function cancelPlusClose() {
  if (plusHoverTimer) {
    clearTimeout(plusHoverTimer);
    plusHoverTimer = null;
  }
}

function schedulePlusClose() {
  cancelPlusClose();
  // 保护一：焦点在面板内（搜索框有光标）。面板会随搜索结果变矮，鼠标可能被
  // 布局变化「抽」到面板外触发 mouseleave —— 此刻不能关，否则就是
  // 「搜索一下就消失」。失焦后再判断（见 onSubpanelFocusOut）。
  if (subpanelFocused.value) return;
  // 保护二：鼠标还停在面板上
  if (subpanelHovered.value) return;
  // 350ms：给「从菜单项斜移进子面板」留足时间（250ms 在面板较远时不够用）。
  // 真正的兜底是 .skill-submenu::before 的热桥，这里只是余量。
  plusHoverTimer = setTimeout(() => {
    plusHoverTimer = null;
    subpanelHovered.value = false;
    subpanelFocused.value = false;
    showPlusMenu.value = false;
    subpanelCategory.value = null;
    knowledgeScope.value?.closePopover();
  }, 350);
}

/** 进入面板：取消关闭计时（面板会变矮，鼠标可能中途「悬空」，靠这个标志兜住） */
function onSubpanelEnter() {
  subpanelHovered.value = true;
  cancelPlusClose();
}

function onSubpanelLeave() {
  subpanelHovered.value = false;
  schedulePlusClose();
}

/** 焦点进入面板（搜索框 / 行按钮）：锁定，不因鼠标移出而关闭 */
function onSubpanelFocusIn() {
  subpanelFocused.value = true;
  cancelPlusClose();
}

/** 焦点离开面板：只有在面板内部换焦点才算没走（focusout 早于新的 focusin） */
function onSubpanelFocusOut(e) {
  if (
    e &&
    e.relatedTarget &&
    e.currentTarget &&
    e.currentTarget.contains(e.relatedTarget)
  )
    return;
  subpanelFocused.value = false;
  if (!subpanelHovered.value) schedulePlusClose();
}

function hoverPlusItem(category, event) {
  cancelPlusClose();
  // 知识库与技能/专家一致：悬停即展开面板，无需点击
  if (category === "knowledge") {
    subpanelCategory.value = null;
    const anchor =
      plusMenuListRef.value?.querySelector(".plus-menu-item-knowledge") ||
      event?.currentTarget;
    knowledgeScope.value?.openPopover(anchor);
    return;
  }
  // 切到其他菜单项时收起知识库面板，避免两个浮层叠着
  knowledgeScope.value?.closePopover();
  if (category === "file") {
    // 无子面板的项：收起子面板，主菜单保持
    subpanelCategory.value = null;
    return;
  }
  openSubpanel(category, event, true);
}

async function loadPlusList(category) {
  try {
    if (category === "experts") {
      // 并行拉取：我的专家第 1 页（服务端 status=active + 分页） + 公共专家（内置目录，全量）
      const [mine, preset] = await Promise.all([
        listExperts({
          status: "active",
          page: 1,
          pageSize: PLUS_EXPERTS_PAGE_SIZE,
        }),
        listPublicExperts(),
      ]);
      // 期间用户已切到别的分类：这批结果直接丢弃，避免串到技能 / 连接器面板
      if (plusListCategory.value !== "experts") return;
      myExperts.value = mine.items || [];
      myExpertsTotal.value = Number(mine.total) || 0;
      myExpertsPage.value = 1;
      publicExperts.value = preset.items || [];
    } else if (category === "skills") {
      const data = await getUserSkills();
      plusList.value = (data.skills || []).map(s => ({
        key: `skill:${s.dir_name}`,
        name: s.name,
        desc: s.description || "暂无描述",
        icon: s.icon || "",
        initial: (s.name || s.dir_name).slice(0, 1).toUpperCase(),
        tag: s.enabled ? "已启用" : "已停用",
        tagType: s.enabled ? "on" : "off",
      }));
    } else if (category === "connectors") {
      const data = await getConnectors();
      // connectorId = 会话开关的标识，也是 mcp.json 的 server key（目录条目用 id，
      // 自定义服务用 server 名）；mcp.json 层面已停用的项仍列出，但标出「已停用」。
      const installed = (data.connectors || [])
        .filter(c => c.installed)
        .map(c => ({
          key: `connector:${c.id}`,
          connectorId: c.id,
          name: c.name,
          desc: c.description || "暂无描述",
          icon: "",
          initial: (c.name || c.id).slice(0, 1).toUpperCase(),
          // mcp.json 里的启用状态：会话开关必须与它一致（停用 = 开关关闭），
          // 「专家·技能·连接器」面板显示的也是这个值
          enabled: c.enabled !== false,
          // 启用时展示的 tag 单独存一份：开关来回切时按它还原（停用统一显示「已停用」）
          enabledTag: "已添加",
          enabledTagType: "on",
          tag: c.enabled === false ? "已停用" : "已添加",
          tagType: c.enabled === false ? "off" : "on",
        }));
      const custom = (data.custom || []).map(c => ({
        key: `custom:${c.name}`,
        connectorId: c.name,
        name: c.name,
        desc: "用户自定义 MCP 服务",
        icon: "",
        initial: (c.name || "").slice(0, 1).toUpperCase(),
        enabled: c.enabled !== false,
        enabledTag: "自定义",
        enabledTagType: "custom",
        tag: c.enabled === false ? "已停用" : "自定义",
        tagType: c.enabled === false ? "off" : "custom",
      }));
      plusList.value = [...installed, ...custom];
    } else {
      plusList.value = [];
    }
  } catch (e) {
    plusListError.value = e.message || "加载失败";
  } finally {
    plusListLoading.value = false;
    // 首屏数据到位后才是真实高度：重算一次（高度锁只增，所以这里只会把占位值矫正掉）
    nextTick(syncSubpanelBox);
  }
}

// ========== 专家快捷面板：行渲染与选择 ==========
// 头像取字与配色统一走 utils/expertAvatar.js（全局 8 色板 .ea-0 ~ .ea-7）

/** 行右侧的浅灰小字描述：优先功能描述，回退专家描述 */
function expertDesc(item) {
  return (
    String(item?.export_description || "")
      .replace(/\s*\n+\s*/g, " ")
      .trim() ||
    String(item?.export_prompt || "")
      .replace(/\s*\n+\s*/g, " ")
      .trim() ||
    "暂无描述"
  );
}

/** 是否为当前会话已召唤的专家（activeExpert 由 App.vue 传入） */
function isExpertSelected(item) {
  if (!item || !props.activeExpert) return false;
  return props.activeExpert.export_agent_id === item.export_agent_id;
}

/**
 * 点击专家行：与专家 Tab 的「召唤」同一链路。
 * 已选中的再点一次 = 取消召唤（同时清掉输入框右下角的专家胶囊）。
 * 操作完成后直接关闭面板（选中态由胶囊与「已选」标承载）。
 */
function toggleExpert(item) {
  if (!item || !item.export_agent_id) return;
  if (isExpertSelected(item)) {
    emit("clearExpert");
  } else {
    emit("selectExpert", item);
  }
  if (props.compact) return;
  showPlusMenu.value = false;
  subpanelCategory.value = null;
  knowledgeScope.value?.closePopover();
  cancelPlusClose();
}

/**
 * 拉「我的专家」的某一页（服务端分页 + 关键字过滤）。
 *
 * page=1 替换列表（首次打开 / 搜索），page>1 追加（点「点击加载更多」）。
 * 请求序号保证搜索时连续输入的多个请求只认最后一次；失败只提示、不清空已加载内容，
 * 用户再点一次「点击加载更多」即可重试。
 */
async function loadMyExpertsPage(page = 1) {
  const seq = (expertsRequestSeq += 1);
  myExpertsLoading.value = true;
  myExpertsError.value = "";
  try {
    const data = await listExperts({
      keyword: expertsKeyword.value,
      status: "active",
      page,
      pageSize: PLUS_EXPERTS_PAGE_SIZE,
    });
    if (seq !== expertsRequestSeq) return;
    const items = data.items || [];
    myExperts.value = page === 1 ? items : myExperts.value.concat(items);
    myExpertsTotal.value = Number(data.total) || 0;
    myExpertsPage.value = page;
  } catch (e) {
    if (seq !== expertsRequestSeq) return;
    myExpertsError.value = e?.message || "加载失败";
  } finally {
    if (seq === expertsRequestSeq) {
      myExpertsLoading.value = false;
      // 条数变了，面板高度要跟着重算（变高才允许向上长）
      nextTick(syncSubpanelBox);
    }
  }
}

/** 「点击加载更多」：再取一页追加；已加载满 total 时按钮本身不渲染 */
function loadMoreMyExperts() {
  if (myExpertsLoading.value) return;
  if (myExperts.value.length >= myExpertsTotal.value) return;
  loadMyExpertsPage(myExpertsPage.value + 1);
}

/** 公共专家本地分页：再放 3 条 */
function loadMorePublicExperts() {
  if (publicExpertsLimit.value >= filteredPublicExperts.value.length) return;
  publicExpertsLimit.value += PUBLIC_EXPERTS_MORE_STEP;
  // 内容变多是唯一允许面板长高的场景（向上长），重算一次尺寸
  nextTick(syncSubpanelBox);
}

// 列表底部入口：跳到「专家·技能·连接器」面板做完整管理（启用/停用、编辑等）
function handleMenuPanel(tab) {
  showPlusMenu.value = false;
  subpanelCategory.value = null;
  emit("openPanel", tab);
}

// ========== 技能选择弹出面板 ==========
// 从「+」菜单的「技能」项右侧弹出（样式对齐参考稿）：顶部搜索、技能列表、
// 底部「从本地添加技能 / 管理技能」。点击技能项把它以令牌形式插入输入框。
const skillSubmenuRef = ref(null);
const skillSubmenuStyle = ref({});
const skillSearch = ref("");
const skillList = ref([]);
const skillListLoading = ref(false);
const skillListError = ref("");
const skillImportInputRef = ref(null);
// 子面板 DOM（技能/专家/连接器三者互斥，共用同一个 ref）：拿真实高度算尺寸，见 syncSubpanelBox。
const subpanelEl = ref(null);
// 一级菜单（.plus-menu）的视口坐标（顶边 / 底边）。
// 子面板的底边活动范围就是 [plusMenuTop, plusMenuBottom]：打开时贴底边（左右两面板
// 底部齐平），内容变少时底边往上收，最多收到与顶边齐平（面板整个落到菜单上方）。
// 只在 openSubpanel 里写一次，之后 syncSubpanelBox 反复读。
const plusMenuTop = ref(0);
const plusMenuBottom = ref(0);
// 本次打开以来量到过的「最满」高度，作为顶边基准（见 syncSubpanelBox）。
const subpanelRefHeight = ref(0);
// 关闭保护：鼠标是否停在面板上、焦点是否在面板内（搜索框）。
// 面板会随搜索结果变矮，鼠标可能被「抽走」到面板外 —— 聚焦期间一律不自动关闭，
// 等失焦且鼠标也不在面板上时才关（见 schedulePlusClose）。
const subpanelHovered = ref(false);
const subpanelFocused = ref(false);

// 图标方块配色（参考稿中的彩色字母方块），按列表顺序循环取色
const SKILL_ICON_COLORS = [
  { bg: "#fde68a", fg: "#92400e" },
  { bg: "#bbf7d0", fg: "#166534" },
  { bg: "#c7d2fe", fg: "#3730a3" },
  { bg: "#fbcfe8", fg: "#9d174d" },
  { bg: "#bae6fd", fg: "#075985" },
  { bg: "#ddd6fe", fg: "#5b21b6" },
];

const filteredSkillList = computed(() => {
  const kw = skillSearch.value.trim().toLowerCase();
  if (!kw) return skillList.value;
  // 与 / 技能选择面板同一套规则：先按技能名/目录名匹配，描述（大段英文说明）只作兜底，
  // 否则输入单个字母（如 m）会把整张列表都匹配出来，看起来像「搜索没生效」。
  const byName = skillList.value.filter(
    s =>
      s.name.toLowerCase().includes(kw) ||
      (s.dir_name || "").toLowerCase().includes(kw),
  );
  if (byName.length > 0) return byName;
  return skillList.value.filter(s => s.desc.toLowerCase().includes(kw));
});

/**
 * 打开二级浮动面板（主菜单常驻），并给首帧一个占位尺寸；真实尺寸与位置由
 * syncSubpanelBox 负责。垂直规则：打开时底边与一级菜单齐平，内容变少时顶边不动、
 * 底边随之上收（最多收到菜单顶边）。不用固定 bottom：内容变矮会让搜索框掉出面板，
 * 由「聚焦期间不自动关闭」兜住（见 schedulePlusClose）。
 */
function openSubpanel(category, event, fromHover = false) {
  // hover 触发时同面板已打开则保持（避免鼠标在项与面板间移动导致闪烁）；
  // click 触发保持翻转语义（再次点击同一项关闭）
  if (subpanelCategory.value === category) {
    if (fromHover) return;
    subpanelCategory.value = null;
    return;
  }
  subpanelCategory.value = category;
  // 换面板时先清掉可能残留的关闭计时器（例如从搜索框失焦后立刻点了另一个菜单项）
  cancelPlusClose();
  subpanelFocused.value = false;
  subpanelHovered.value = false;
  subpanelRefHeight.value = 0;
  const anchor = event.currentTarget;
  const rect = anchor.getBoundingClientRect();
  // 对齐基准是一级菜单容器（.plus-menu）：底边决定「齐平」，顶边是上收的极限
  const menuEl = anchor.closest ? anchor.closest(".plus-menu") : null;
  const menuRect = menuEl ? menuEl.getBoundingClientRect() : null;
  plusMenuTop.value = menuRect ? menuRect.top : rect.top;
  plusMenuBottom.value = menuRect ? menuRect.bottom : rect.bottom;
  // 宽度与 CSS 同源，并受视口宽度约束（窄窗口下不许溢出）
  const width = Math.min(SUBPANEL_WIDTH, window.innerWidth - 16);
  const fitsRight = rect.right + SUBPANEL_GAP + width <= window.innerWidth - 8;
  const left = fitsRight
    ? rect.right + SUBPANEL_GAP
    : Math.max(8, rect.left - SUBPANEL_GAP - width);
  // 占位：真实高度未知（列表异步加载）。先按「底边贴菜单底边、上限 = 菜单竖向空间」摆一下，
  // sync 随后用真实内容高度校正。height 必须给 auto —— 像素值会把面板撑住。
  const provisionalMax = Math.max(
    120,
    Math.min(
      SUBPANEL_MAX_HEIGHT,
      plusMenuBottom.value - Math.max(8, plusMenuTop.value),
    ),
  );
  skillSubmenuStyle.value = {
    position: "fixed",
    left: `${left}px`,
    width: `${width}px`,
    top: `${Math.max(8, plusMenuBottom.value - provisionalMax)}px`,
    height: "auto",
    maxHeight: `${provisionalMax}px`,
  };
  // 三个面板共用一套标题/占位/计数文案（PLUS_LIST_META），技能面板也要走到这里，
  // 否则标题行与搜索占位会是空的（技能列表走 loadSkillSubList，不经 openPlusList）。
  plusListCategory.value = category;
  // 三个面板共用一份搜索词：切分类时一律清空，避免把上一个面板的关键字带过来
  subpanelKeyword.value = "";
  if (category === "skills") {
    skillSearch.value = "";
    loadSkillSubList();
  } else {
    openPlusList(category);
  }
  nextTick(syncSubpanelBox);
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
  const el = subpanelEl.value;
  const dom = el && el.$el ? el.$el : el;
  if (!dom) return;
  const viewportH = window.innerHeight;
  // 拿不到基准时退化到视口下半部，保证仍能算出一个合理位置
  const menuTop = plusMenuTop.value || Math.round(viewportH * 0.6);
  const menuBottom = plusMenuBottom.value || menuTop + 40;
  // 可用空间 = 视口顶到菜单底边（底边最低就贴到这里）
  const cap = Math.max(120, Math.min(SUBPANEL_MAX_HEIGHT, menuBottom - 8));
  // 量「内容自然高度」之前必须把上一轮写进去的 height / max-height 都放开，
  // 否则量到的是被压扁的那份高度。
  dom.style.height = "auto";
  dom.style.maxHeight = "none";
  const natural = dom.offsetHeight;
  // 拿不到高度（面板还没布局）就别动样式，否则会被压成 1px
  if (!natural) return;
  const height = Math.min(natural, cap);
  // 本次打开以来量到过的最大内容高度 = 顶边基准（只增不减）：
  // 搜索过滤后顶边不许跟着往下滑，只能底边上收。
  if (height > subpanelRefHeight.value) subpanelRefHeight.value = height;
  const refHeight = subpanelRefHeight.value || height;
  // 基准顶边：最满时底边正好贴菜单底边 → 打开时左右两面板底部齐平
  let top = menuBottom - refHeight;
  // 上收极限：底边最多收到与「菜单顶边」齐平（判据用底边，不能用 top）
  if (top + height < menuTop) top = menuTop - height;
  // 兜底：不许越过菜单底边、不许钻出视口顶
  top = Math.max(8, Math.min(top, menuBottom - height));
  // ★ 关键：不写死 height 像素，只给 max-height。面板高度交给内容决定，
  // 搜索后条数变少 → 面板立刻变矮、下边框上收（2026-10-24）；
  // 即使 offsetHeight 量偏，也只影响 top 与上限，不会把面板撑住。
  dom.style.maxHeight = `${refHeight}px`;
  skillSubmenuStyle.value = {
    ...skillSubmenuStyle.value,
    top: `${top}px`,
    height: "auto",
    maxHeight: `${refHeight}px`,
  };
}

async function loadSkillSubList() {
  skillListLoading.value = true;
  skillListError.value = "";
  try {
    const data = await getUserSkills();
    rememberSkills(data.skills || []);
    skillList.value = (data.skills || []).map((s, i) => {
      const name = s.name || s.dir_name;
      const color = SKILL_ICON_COLORS[i % SKILL_ICON_COLORS.length];
      return {
        dir_name: s.dir_name,
        name,
        desc: s.description || "暂无描述",
        icon: s.icon || "",
        category_id: s.category_id,
        enabled: !!s.enabled,
        initial: name.slice(0, 1).toUpperCase(),
        iconBg: color.bg,
        iconFg: color.fg,
      };
    });
  } catch (e) {
    skillListError.value = e.message || "加载技能失败";
  } finally {
    skillListLoading.value = false;
    // 首屏数据到位后才是真实高度：重算一次（高度锁只增，所以这里只会把占位值矫正掉）
    nextTick(syncSubpanelBox);
  }
}

// ========== 技能标签：插入输入框文本，标识本会话启用该技能 ==========
// 「/ 唤起」与「+」→ 技能面板共用同一套文本标签（见 utils/skillPillTokens.js）：
// 标签落在光标处，选中后光标停在标签之后，删除整段进行。
function isSkillSelected(dirName) {
  return selectedSkillDirs.value.includes(dirName);
}

/**
 * 「+」→ 技能子面板点行：挂载 / 取消技能标签，行为与「/」唤起技能完全一致。
 * 停用技能不参与：Agent 只加载已启用的技能，挂上去也不会生效。
 */
function onSkillSubItemClick(item) {
  if (!item || !item.dir_name || !item.enabled) return;
  toggleSkillToken(item);
}

function toggleSkillToken(item) {
  if (isSkillSelected(item.dir_name)) {
    removeSkillToken(item.dir_name);
    return;
  }
  rememberSkills([{ ...item, description: item.description || item.desc }]);
  const ta = textareaRef.value;
  const base = ta?.value ?? message.value;
  const caret =
    typeof ta?.selectionStart === "number" ? ta.selectionStart : base.length;
  replaceTextWithCaret(insertSkillToken(base, caret, tokenForSkill(item)));
  console.log(`[${new Date().toISOString()}] [技能选择] ${item.dir_name}`);
}

// ========== 连接器开关（「+」→ 连接器，替代旧的「点行插入输入框胶囊」） ==========
// 开关直接对应连接器自身的启用状态（用户 mcp.json 里每个 server 的 enabled），
// 与「专家·技能·连接器」面板是同一份状态：这里改，那边实时跟着变（见 connectorSync.js）。
// 正在提交的连接器 id（该行开关暂时禁用，避免连点产生竞态）。
const connectorToggling = ref("");
// 操作反馈：改完开关在面板内提示一句，3s 后自动消失
const connectorNotice = ref("");
const connectorNoticeType = ref("success");
let connectorNoticeTimer = null;
// 连接器状态变更事件的取消订阅函数（onMounted 注册、onUnmounted 解绑）
let offConnectorsChanged = null;
// 面板内的搜索词已统一为 subpanelKeyword（见上方「+」二级列表区域）

/** 连接器行在会话开关里的标识（目录条目用 id，自定义服务用 server 名） */
function connectorKeyOf(item) {
  return (item && item.connectorId) || "";
}

/**
 * 【技能 / 连接器】面板的搜索结果。
 * 名称（含技能目录名）优先命中；描述是一大段说明，几乎每个词都能命中，
 * 因此只在名称完全没命中时才回退到描述（与「/」检索同一取舍）。
 */
const filteredPlusList = computed(() => {
  const kw = subpanelKeyword.value.trim().toLowerCase();
  if (!kw) return plusList.value;
  const byName = plusList.value.filter(item => {
    const name = String(item.name || "").toLowerCase();
    const dir = String(item.dir_name || "").toLowerCase();
    return name.includes(kw) || dir.includes(kw);
  });
  if (byName.length > 0) return byName;
  return plusList.value.filter(item =>
    String(item.desc || "")
      .toLowerCase()
      .includes(kw),
  );
});

/**
 * 连接器在 mcp.json 里是否启用。
 * 「专家·技能·连接器」面板的开关读的是同一个字段：两处必须显示同一状态。
 */
function connectorEnabled(item) {
  return !!item && item.enabled !== false;
}

/** 该连接器的开关是否为「打开」 */
function isConnectorOn(item) {
  return !!connectorKeyOf(item) && connectorEnabled(item);
}

/**
 * 开关是否不可点。
 * 启用与停用都允许在本面板直接切换（与「专家·技能·连接器」面板同一份状态），
 * 只有「正在提交该行」时才临时禁用，避免连点产生竞态。
 */
function isConnectorSwitchDisabled(item) {
  const key = connectorKeyOf(item);
  if (!key) return true;
  return connectorToggling.value === key;
}

/** 该行开关是否正在提交（显示忙碌态的转圈） */
function isConnectorBusy(item) {
  const key = connectorKeyOf(item);
  return !!key && connectorToggling.value === key;
}

/** 开关的无障碍名称 */
function connectorSwitchLabel(item) {
  if (isConnectorBusy(item)) return `正在保存连接器 ${item.name} 的开关`;
  return `${isConnectorOn(item) ? "停用" : "启用"}连接器 ${item.name}`;
}

/** 开关的 title：说清点下去会发生什么、以及会同步到哪里 */
function connectorSwitchTitle(item) {
  if (isConnectorBusy(item)) return "正在保存…";
  return isConnectorOn(item)
    ? "已启用（点击停用，「专家·技能·连接器」面板同步生效）"
    : "已停用（点击启用，「专家·技能·连接器」面板同步生效）";
}

/** 面板内的操作反馈（连接器开关），ms 后自动消失 */
function showConnectorNotice(message, type = "success", ms = 2600) {
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer);
  connectorNotice.value = message;
  connectorNoticeType.value = type;
  connectorNoticeTimer = setTimeout(() => {
    connectorNoticeTimer = null;
    connectorNotice.value = "";
  }, ms);
}

/** 清掉提示（打开面板时调用，避免看到上一次操作留下的文案） */
function clearConnectorNotice() {
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer);
  connectorNoticeTimer = null;
  connectorNotice.value = "";
}

/** 按最新状态刷新某个连接器行的开关与 tag（tag 停用时统一显示「已停用」） */
function applyConnectorState(id, enabled) {
  if (!id) return;
  const item = plusList.value.find(i => connectorKeyOf(i) === id);
  if (!item) return;
  item.enabled = !!enabled;
  item.tag = item.enabled ? item.enabledTag || "" : "已停用";
  item.tagType = item.enabled ? item.enabledTagType || "on" : "off";
}

/** 其它面板（「专家·技能·连接器」）改了开关：同步本面板显示（忽略自己派发的回声） */
function onConnectorsChangedHandler(payload) {
  if (!payload || payload.source === "plus-panel") return;
  applyConnectorState(payload.id, payload.enabled);
}

/**
 * 切换连接器的启用状态：直接改连接器自身的开关（后端写用户 mcp.json 的 enabled，
 * 全局生效），并广播给「专家·技能·连接器」面板，保证两处状态一致。
 * 启用 → 停用、停用 → 启用都走这里，两个面板都可以操作。
 */
async function toggleConnectorSwitch(item) {
  const id = connectorKeyOf(item);
  if (!id) return;
  // 同一行正在提交时忽略连点（按钮此时也已 disabled）
  if (connectorToggling.value) return;
  const next = !connectorEnabled(item);
  connectorToggling.value = id;
  // 本面板先乐观更新（点下去立即有反馈，不等接口往返）
  applyConnectorState(id, next);
  try {
    await toggleConnector(id, next);
    // 只在真正改成功后才广播：对方收到后会重新拉取，若在请求途中广播，
    // 它拉到的还是旧数据，会出现「跟着变一下又弹回去」的抖动
    emitConnectorsChanged(id, next, "plus-panel");
    console.log(
      `[${new Date().toISOString()}] [连接器开关] ${item.name} -> ${next ? "启用" : "停用"}`,
    );
    showConnectorNotice(
      `已${next ? "启用" : "停用"}「${item.name}」，已同步到「专家·技能·连接器」`,
    );
  } catch (e) {
    // 后端没改成功：只回滚本面板（对方从未收到通知，保持原状态即可）
    applyConnectorState(id, !next);
    showConnectorNotice(e.message || "切换连接器开关失败", "error", 4000);
  } finally {
    connectorToggling.value = "";
  }
}

/** 收起「+」菜单与其二级面板（含待执行的悬停关闭定时器） */
function hidePlusMenu() {
  showPlusMenu.value = false;
  subpanelCategory.value = null;
  cancelPlusClose();
}

// 会话切换：任何 sessionId 变化都算进入「会话展示阶段」（切历史会话 / 新建会话 /
// 从新建态点开历史会话），令牌一律复位，不得残留到历史会话的输入框里。
// 唯一例外是本组件自己发起的建会话（上传文件 / 切换连接器开关），由对应链路「先存后补」
// （见 ensureSessionId），且不算真正的会话切换、不收起「+」面板。
let selfCreatedSession = false;

watch(
  () => props.sessionId,
  (newVal, oldVal) => {
    if (newVal === oldVal) return;
    restoreInitialReferences(newVal, oldVal);
    closeReference();
    // 会话已切换/已创建：下一次发送需要按新的 sessionId 重新判断
    pendingSessionPromise = null;
    // 本组件自己发起的建会话（上传文件 / 切换连接器开关）仍属「输入阶段的连续操作」，
    // 不要收起正在用的「+」面板（否则点一下开关面板就消失，看不出刚做了什么）。
    const ownCreate = selfCreatedSession;
    selfCreatedSession = false;
    if (!ownCreate) subpanelCategory.value = null;
    // 技能标签是文本的一部分：切会话时把标签从草稿里剥掉（等价于旧的「令牌复位」），
    // 避免标签跟着草稿残留到历史会话的输入框里；本组件自己发起的建会话不算切换，保留标签。
    if (!ownCreate)
      message.value = stripSkillTokens(message.value, tokenMap.value);
    // 切到其他会话/回到新对话：新对话暂存的知识选择不再适用
    if (!newVal || (oldVal && newVal !== oldVal))
      pendingKnowledgeBaseIds.value = [];
    // 注：连接器开关不随会话走（它反映连接器自身的启用状态，与会话无关）
    subpanelKeyword.value = "";
  },
);

// ========== 从本地添加技能（.zip 导入） ==========
function handleSkillImport() {
  skillImportInputRef.value?.click();
}

async function handleSkillImportFile(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  skillListLoading.value = true;
  skillListError.value = "";
  try {
    await importSkill(file);
    await loadSkillSubList();
  } catch (e) {
    skillListError.value = e.message || "导入技能失败";
    skillListLoading.value = false;
  }
}

const canSend = computed(() => {
  return (
    stripSkillTokens(message.value, tokenMap.value).trim() ||
    fileReferences.value.length > 0 ||
    uploadedFiles.value.length > 0 ||
    selectedSkillDirs.value.length > 0
  );
});

const showTokenPopup = ref(false);

// ========== 会话耗时 ==========
// 后台未返回耗时数据（sessionDuration<=0）且「会话信息」弹窗打开时，前端按 1s
// 间隔本地计时，让会话耗时实时更新；后台有数据时直接用后台值。
const formattedDuration = useContextDuration(props, showTokenPopup);

// 联网开关的真身在本组件，但发送动作不止「输入框回车」一条：消息下方「重新发送」
// 走的是页面层的 handleRetry，不经过这里。父级没有镜像就只能用默认值 false 发请求，
// 于是出现「按钮亮着、请求却是关网」。所以状态一变就上报，immediate 保证挂载时先对齐一次。
watch(webSearchEnabled, val => emit("webSearchChange", Boolean(val)), {
  immediate: true,
});

// 搜索词一变，列表条数就变，面板高度必须跟着重算（底边会随条数上下浮动，
// 变多/变少都要重算；是否关闭由 schedulePlusClose 的「聚焦保护」负责）。
// 专家面板额外做一件事：关键字交给接口查（服务端分页），本地只保留公共专家那半区的过滤。
watch(subpanelKeyword, val => {
  nextTick(syncSubpanelBox);
  if (subpanelCategory.value !== "experts") return;
  const kw = String(val || "").trim();
  // 面板打开时会主动清空搜索词（openPlusList），那不是用户输入，不该再打一次接口
  if (kw === expertsKeyword.value) return;
  expertsKeyword.value = kw;
  if (expertsSearchTimer) clearTimeout(expertsSearchTimer);
  expertsSearchTimer = setTimeout(() => {
    expertsSearchTimer = null;
    // 防抖窗口内面板可能已关闭 / 已切到别的分类
    if (subpanelCategory.value !== "experts") return;
    myExperts.value = [];
    myExpertsTotal.value = 0;
    myExpertsPage.value = 0;
    loadMyExpertsPage(1);
  }, EXPERT_SEARCH_DEBOUNCE_MS);
});
// 技能面板用的是自己的 skillSearch（不是 subpanelKeyword）：漏了这一条，在技能面板里
// 搜索时列表变了但面板高度不重算 —— 表现就是「搜索后高度不跟着收，只有专家面板对」。
watch(skillSearch, () => nextTick(syncSubpanelBox));

function toggleTokenPopup() {
  showTokenPopup.value = !showTokenPopup.value;
}

function closeTokenPopup() {
  if (showTokenPopup.value) {
    showTokenPopup.value = false;
  }
}

const { showTokenRing, contextPercent, contextColor } = useContextRing(props);

const ringRef = ref(null);

const popupStyle = computed(() => {
  if (!ringRef.value) return {};
  const rect = ringRef.value.getBoundingClientRect();
  return {
    position: "fixed",
    bottom: `${window.innerHeight - rect.top + 10}px`,
    right: `${window.innerWidth - rect.right - 8}px`,
  };
});

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return n.toString();
}

/**
 * 确保会话存在并返回 sessionId：并行上传共享同一个创建 Promise，避免重复建会话。
 * 仅在发送阶段调用：附件先以待上传状态留在前端，点发送才创建会话并上传。
 */
function ensureSessionId() {
  if (props.sessionId) return Promise.resolve(props.sessionId);
  if (!pendingSessionPromise) {
    pendingSessionPromise = new Promise((resolve, reject) => {
      let unwatch = null;
      // 会话创建失败（接口异常等）时避免上传链路永久挂起
      const timer = setTimeout(() => {
        if (unwatch) unwatch();
        reject(new Error("会话创建超时，请重试"));
      }, 15000);
      unwatch = watch(
        () => props.sessionId,
        newSessionId => {
          if (!newSessionId) return;
          clearTimeout(timer);
          unwatch();
          resolve(newSessionId);
        },
      );
      // 触发会话创建（标记为「自己发起」，watcher 据此不收起「+」面板）
      selfCreatedSession = true;
      emit("createSession");
    }).catch(error => {
      pendingSessionPromise = null;
      throw error;
    });
  }
  return pendingSessionPromise;
}

/**
 * 长文本转附件：正文写进会话工作区的 txt，输入框只留开头一段 + 文件引用。
 * 引用走 file_references（agent 按需分块读取）；/compact 压缩不了本轮输入，故走独立入口。
 */
async function offloadLongMessage() {
  if (offloadingLongMessage.value || !needsOffload(message.value)) return;
  const text = message.value;
  const fileName = longMessageFileName();
  offloadingLongMessage.value = true;
  try {
    const sessionId = await ensureSessionId();
    if (!sessionId) throw new Error("会话创建失败");
    const response = await uploadFile(
      sessionId,
      new File([longMessageFileContent(text)], fileName, {
        type: "text/plain",
      }),
    );
    const path = response?.parsed_path;
    // file_context 关闭或正文解析失败时拿不到可引用的工作区路径：保留正文，别让用户以为已保存
    if (!path) throw new Error("附件正文未就绪，请稍后重试");
    fileReferences.value = addReference(fileReferences.value, {
      path,
      type: "file",
    });
    message.value = longMessageNotice(text, fileName);
  } catch (error) {
    showUploadNotice(error?.message || "长文本转附件失败");
  } finally {
    offloadingLongMessage.value = false;
  }
}

/** 单个文件的上传流程（各文件并行执行，互不阻塞） */
async function uploadOneFile(fileItem) {
  const file = fileItem.file;
  try {
    const sessionId = await ensureSessionId();
    if (!sessionId) {
      throw new Error("会话创建失败");
    }

    // 等待会话创建期间被用户点 × 删除：不再上传
    if (fileItem.cancelled) return;

    // 调用实际的上传接口；holder 用于外部中断（删除上传中的文件）
    const holder = {};
    fileItem.xhrHolder = holder;
    const response = await uploadFile(
      sessionId,
      file,
      progress => {
        // 找到对应的文件项并更新进度
        if (fileItem.cancelled) return;
        const index = uploadedFiles.value.findIndex(f => f.file === file);
        if (index !== -1) {
          uploadedFiles.value[index].uploadProgress = progress;
          // 强制触发更新
          uploadedFiles.value = [...uploadedFiles.value];
        }
      },
      holder,
    );

    // 上传期间被用户删除：丢弃结果
    if (fileItem.cancelled) return;

    // 找到对应的文件项并更新状态
    const index = uploadedFiles.value.findIndex(f => f.file === file);
    if (index !== -1) {
      uploadedFiles.value[index].uploadStatus = "completed";
      uploadedFiles.value[index].uploadProgress = 100;
      uploadedFiles.value[index].filePath = response.file_path;
      uploadedFiles.value[index].id = response.id;
      // 强制触发更新
      uploadedFiles.value = [...uploadedFiles.value];
    }

    console.log("文件上传成功:", response);
  } catch (error) {
    // 用户主动取消，不算失败
    if (fileItem.cancelled) return;
    // 找到对应的文件项并更新状态
    const index = uploadedFiles.value.findIndex(f => f.file === file);
    if (index !== -1) {
      const reason = error?.message || "上传失败";
      // 超过单文件大小限制的失败不给「重试」（重试也没用），其余可重试
      const isSizeLimit =
        error?.code === "UPLOAD_FILE_TOO_LARGE" ||
        /不能超过\s*\d+(\.\d+)?\s*MB/.test(reason);
      uploadedFiles.value[index].uploadStatus = "error";
      uploadedFiles.value[index].errorMessage = reason;
      uploadedFiles.value[index].retryable = !isSizeLimit;
      // 强制触发更新
      uploadedFiles.value = [...uploadedFiles.value];
    }
    console.error("文件上传失败:", error);
  }
}

/** 重新上传一个失败的文件（复用同一个 File 对象，无需用户重新选择） */
function retryUpload(index) {
  const item = uploadedFiles.value[index];
  if (!item || item.uploadStatus !== "error" || !item.retryable) return;

  // 失败文件不占额度，所以重试等价于「新增一个文件」，同样受限额约束：
  // 已满名额 / 超出剩余空间 / 超过单文件上限 时不允许重新上传
  const L = uploadLimits.value;
  if (L.enabled) {
    if (usedCount.value + 1 > L.maxFiles) {
      showUploadNotice(
        `最多只能上传 ${L.maxFiles} 个文件（已用 ${usedCount.value} 个），无法重新上传 ${item.filename}`,
      );
      return;
    }
    if (item.size > mbToBytes(L.maxFileSizeMb)) {
      showUploadNotice(
        `${item.filename} 超过单文件上限 ${L.maxFileSizeMb}MB，无法重新上传`,
      );
      return;
    }
    if (usedBytes.value + item.size > mbToBytes(L.maxTotalMb)) {
      showUploadNotice(
        `${item.filename} 超出剩余空间 ${formatSize(remainBytes.value)}，无法重新上传`,
      );
      return;
    }
  }

  // 重试同样遵循「发送时才上传」：回到待发送状态，用户点发送时再上传。
  item.uploadStatus = "pending";
  item.uploadProgress = 0;
  item.errorMessage = "";
  item.retryable = false;
  item.cancelled = false;
  uploadedFiles.value = [...uploadedFiles.value];
}

async function handleFileSelect(event) {
  const files = Array.from(event.target.files);
  // 先清空 input，避免同一文件二次选择不触发 change
  event.target.value = "";
  if (files.length === 0) return;

  // 入列前校验：通过的文件才入列、才上传
  const result = pickAcceptable(files);

  if (result.batchRejected) {
    // 数量不满足：整批拒绝，让用户重新选择
    showUploadNotice(result.message);
    return;
  }
  if (result.rejected.length > 0) {
    // 单文件超限 / 超出剩余空间：只跳过这些文件，其余照常上传
    showUploadNotice(buildRejectNotice(result, result.accepted.length));
  }

  // 统一「发送时上传」：无论新对话还是已有会话，选完文件都只留在前端（pending），
  // 点发送时 preparePendingFiles 才真正落盘（已有会话直接用当前 sessionId）。
  for (const file of result.accepted) {
    // 创建文件项
    const fileItem = {
      file: file,
      filename: file.name,
      size: file.size,
      uploadStatus: "pending",
      uploadProgress: 0,
      cancelled: false,
      xhrHolder: null,
      errorMessage: "",
      retryable: false,
    };

    // 添加到数组
    uploadedFiles.value.push(fileItem);

    // 强制触发初始渲染
    uploadedFiles.value = [...uploadedFiles.value];
  }
}

async function removeFile(index) {
  const file = uploadedFiles.value[index];
  if (!file) return;

  // 标记取消：排队/等待会话中的不再上传，上传中的立即中断请求
  file.cancelled = true;
  if (
    file.uploadStatus === "uploading" &&
    file.xhrHolder &&
    file.xhrHolder.xhr
  ) {
    try {
      file.xhrHolder.xhr.abort();
    } catch (e) {
      /* 忽略中断异常 */
    }
  }

  // 如果文件已经上传成功，调用后台的删除接口
  if (file.uploadStatus === "completed" && file.id && props.sessionId) {
    try {
      await deleteFile(props.sessionId, file);
      console.log("文件删除成功:", file.filename);
    } catch (error) {
      console.error("文件删除失败:", error);
    }
  }

  // 从前端列表中移除文件
  uploadedFiles.value.splice(index, 1);
  // 强制触发更新
  uploadedFiles.value = [...uploadedFiles.value];
}

/** 发送前把“待发送”附件上传到会话（新对话此时才会创建会话） */
async function preparePendingFiles() {
  const pending = uploadedFiles.value.filter(f => f.uploadStatus === "pending");
  if (!pending.length) return;
  for (const item of pending) {
    if (item.cancelled) continue;
    item.uploadStatus = "uploading";
    item.uploadProgress = 0;
    item.errorMessage = "";
    item.retryable = false;
  }
  uploadedFiles.value = [...uploadedFiles.value];
  await Promise.all(
    pending.filter(item => !item.cancelled).map(item => uploadOneFile(item)),
  );
}

async function send() {
  if (
    !canSend.value ||
    props.disabled ||
    preparingFiles.value ||
    enhancement.value.pending
  )
    return;
  preparingFiles.value = true;
  try {
    // 新对话的知识选择只存在本地：先取快照，再上传附件（上传会先创建会话）
    const knowledgeBaseIds = props.sessionId
      ? []
      : [...pendingKnowledgeBaseIds.value];

    await preparePendingFiles();

    const uploadingFiles = uploadedFiles.value.filter(
      f => f.uploadStatus === "uploading",
    );
    if (uploadingFiles.length > 0) {
      console.log("文件正在上传中，请稍候...");
      return;
    }

    // 只发送上传成功的文件：失败文件（无 id/file_path）不随消息发出、不在气泡中展示
    const failedFiles = uploadedFiles.value.filter(
      f => f.uploadStatus === "error",
    );
    if (failedFiles.length > 0) {
      console.warn(
        `[发送消息] ${failedFiles.length} 个文件上传失败，已从本次发送中剔除:`,
        failedFiles.map(f => f.filename).join(", "),
      );
    }
    const filesToSend = uploadedFiles.value
      .filter(f => f.uploadStatus === "completed")
      .map(f => ({
        id: f.id,
        filename: f.filename,
        size: f.size,
        file: f.file,
        file_path: f.filePath || null,
        type: f.file?.type || f.fileType || "",
      }));

    const payload = buildPayload();
    if (
      !payload &&
      !filesToSend.length &&
      !selectedSkillDirs.value.length &&
      !fileReferences.value.length
    ) {
      // 附件全部失败且没有正文/技能：保留文件与错误提示，不发空请求
      return;
    }

    // 详细日志：记录发送操作、文件上传
    const ts = new Date().toISOString();
    console.log(
      `[${ts}] [发送消息] 内容: "${message.value.substring(0, 100)}" | 文件数: ${filesToSend.length}`,
    );
    if (filesToSend.length > 0) {
      console.log(
        `[${ts}] [文件上传] ${filesToSend.map(f => `${f.filename}(${f.size}B)`).join(", ")}`,
      );
    }

    // 已选技能以 skills（技能目录名）参数单独交给后端；输入框里的内联标签只是 UI，
    // 正文发送前会把标签整段剥掉（否则气泡/标题/历史里会露出 `/技能名` 这类暗号）。
    // 连接器不随消息提交：本会话启用哪些连接器由「+」面板的连接器开关单独设置。
    const turnSkills = selectedSkillDirs.value;
    // 待修改专家 ID（专家面板「编辑」带入，不展示给用户）：随请求结构化字段上行，
    // 后端校验归属后绑定为 save_expert 的修改目标
    const expertEditId = pendingExpertEditId.value;
    emit(
      "send",
      payload,
      filesToSend,
      null,
      true,
      webSearchEnabled.value,
      turnSkills,
      expertEditId,
      knowledgeBaseIds,
      [...fileReferences.value],
      interactionMode.value,
    );

    message.value = "";
    fileReferences.value = [];
    closeReference();
    uploadedFiles.value = [];
    // 新对话选择已随消息提交；已有会话改选由选择器单独落库，这里清掉本地暂存
    if (props.sessionId) pendingKnowledgeBaseIds.value = [];
    // 预选技能与待修改专家已随本条消息提交，清掉避免影响下一条
    preselectedSkillDirs.value = new Set();
    pendingExpertEditId.value = "";
    dismissUploadNotice();
    closeSkillPicker();
    nextTick(() => autoResize());
  } finally {
    preparingFiles.value = false;
  }
}

/**
 * 发送的消息正文：剥掉内联技能标签、只归一化用户输入，不拼任何指令前缀。
 * 已选技能以 skills（目录名）参数带给后端（见 send()）。
 */
function buildPayload() {
  return buildMessageText(stripSkillTokens(message.value, tokenMap.value));
}

function stop() {
  emit("stop");
}

function autoResize() {
  if (textareaRef.value) {
    textareaRef.value.style.height = "auto";
    textareaRef.value.style.height =
      Math.min(textareaRef.value.scrollHeight, TEXTAREA_MAX_HEIGHT) + "px";
    syncMirror(); // 高度变化会改变 textarea 的可滚动区间（scrollTop 被钳制），镜像层要跟上
  }
}

function onInput(event) {
  invalidateEnhancement();
  const ta = event?.target || textareaRef.value;
  // 移动端兜底（见 utils/skillPillTokens.js 的 reconcileTokenDeletion）：Android Chrome 的
  // Backspace 不给 keydown、部分浏览器也没有 beforeinput，浏览器只啃掉标签的一两个字时，
  // 在这里按「编辑前/后文本差分」把整个标签补删干净，避免留下半个标签的残缺文本。
  if (ta && typeof ta.value === "string") {
    const healed = reconcileTokenDeletion(
      lastInputText,
      ta.value,
      ta.selectionStart,
      tokenMap.value,
    );
    if (healed) {
      // replaceTextWithCaret 会复位光标并同步镜像层；lastInputText 由 syncMirror 更新
      replaceTextWithCaret(healed);
      emit("typing");
      return;
    }
  }
  autoResize();
  updateCaretLine();
  updateSkillPicker();
  updateReference(ta);
  syncMirror();
  emit("typing");
}

// 程序化赋值（恢复草稿、切会话、发送后清空）不触发 input 事件，镜像层在这里补同步
watch(message, () => nextTick(syncMirror));

watch(
  () => props.disabled,
  val => {
    if (!val && textareaRef.value) {
      textareaRef.value.focus();
    }
  },
);

onMounted(() => {
  // 恢复刷新前的输入草稿
  try {
    const draft = sessionStorage.getItem(props.draftKey);
    if (draft) {
      const restored = restoreReferenceDraft(draft, props.sessionId);
      message.value = restored.message;
      fileReferences.value = restored.fileReferences;
      nextTick(() => autoResize());
    }
  } catch (e) {
    // 忽略草稿恢复失败
  }

  // 技能列表先拉一次：草稿里可能留着技能标签，需要靠列表把 `／显示名` 反解回技能
  loadSkillOptions()
    .then(() => {
      // 旧版草稿里的半角 / 标签迁移成全角形式（要等技能列表就绪才能识别旧 token）
      if (message.value) {
        const migrated = migrateLegacySkillTokens(
          message.value,
          tokenMap.value,
        );
        if (migrated !== message.value) message.value = migrated;
      }
    })
    .catch(() => {
      /* 列表拉取失败时标签按普通文本显示，不影响输入 */
    });

  // 恢复待选技能（技能中心「创建技能」等入口预置），读取后即清除
  if (props.consumePendingSkill) restorePendingSkill();

  // 消费待修改专家（专家面板「编辑」预置）：ID 存组件状态、不进正文，发送时上行
  try {
    const rawEdit = sessionStorage.getItem(PENDING_EXPERT_EDIT_KEY);
    if (rawEdit) sessionStorage.removeItem(PENDING_EXPERT_EDIT_KEY);
    const parsed = rawEdit ? JSON.parse(rawEdit) : null;
    if (parsed?.expert_agent_id)
      pendingExpertEditId.value = parsed.expert_agent_id;
  } catch (_) {
    /* 隐私模式等场景忽略 */
  }

  syncMirror();
  document.addEventListener("click", closeTokenPopup);
  document.addEventListener("click", closeSkillPickerOnClick);
  window.addEventListener("resize", handleViewportChange);
  document.addEventListener("click", closePlusMenu);
  loadWebSearchStatus();
  loadKnowledgeStatus();
  // 「专家·技能·连接器」面板里改了开关 → 本面板同步（两个面板不在同一组件树分支，走事件广播）
  offConnectorsChanged = onConnectorsChanged(onConnectorsChangedHandler);
  loadUploadLimits();
});

onUnmounted(() => {
  document.removeEventListener("click", closeTokenPopup);
  document.removeEventListener("click", closeSkillPickerOnClick);
  window.removeEventListener("resize", handleViewportChange);
  document.removeEventListener("click", closePlusMenu);
  if (offConnectorsChanged) offConnectorsChanged();
  if (connectorNoticeTimer) clearTimeout(connectorNoticeTimer);
  if (expertsSearchTimer) clearTimeout(expertsSearchTimer);
  cancelPlusClose();
  if (uploadNoticeTimer) clearTimeout(uploadNoticeTimer);
});

// 供父组件把预置问题完整文本填进输入框（卡片上最多显示 3 行，长问题展示不全）
function setMessage(text) {
  message.value = text || "";
  nextTick(() => textareaRef.value?.focus());
}

function getMessage() {
  return message.value;
}

// 供父组件（预置问题卡片点击）走输入框的正常发送链路：复用 send() 的
// 上传等待、canSend/disabled 校验与清空逻辑，不重复实现一份发送
function triggerSend() {
  send();
}

// 供父组件调用：聚焦输入框并把光标移到末尾（如侧边栏打开时）
function focusInput() {
  nextTick(() => {
    const ta = textareaRef.value;
    if (!ta || ta.disabled) return;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  });
}

// 供父组件调用：替换输入内容（如侧边栏的快捷提问、划词提问）
function setDraft(text) {
  message.value = text || "";
  nextTick(() => autoResize());
  focusInput();
}

defineExpose({ setMessage, getMessage, triggerSend, focusInput, setDraft });
</script>

<style scoped src="./ChatInput.css"></style>

<style scoped>
.picker-group {
  margin: 0;
  padding: 0;
  list-style: none;
}
.picker-group + .picker-group {
  margin-top: 12px;
}
.picker-group-title {
  padding: 8px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}
.skill-picker-header {
  justify-content: flex-end;
}
.skill-picker-item.unavailable {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
