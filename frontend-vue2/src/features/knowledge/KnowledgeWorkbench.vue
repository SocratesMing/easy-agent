<template>
  <section class="ke-shell">
    <header class="ke-topbar">
      <div class="ke-topbar-title">
        <button class="ke-icon-button subtle" aria-label="返回对话" title="返回对话" @click="$emit('close')">
          <IconArrowLeft />
        </button>
        <div>
          <h1>知识库</h1>
          <span>把资料变成可检索、可追溯的知识</span>
        </div>
      </div>
      <div class="ke-topbar-actions">
        <button
          v-if="teamSpaceManagement.can_manage && selectedSpace === 'team'"
          class="ke-button admin-access"
          title="配置人员、部门及公共空间创建与管理权限"
          @click="$emit('manage-personnel')"
        ><IconUserRoundCog />人员管理</button>
        <span class="ke-health" :class="healthClass"><i></i>{{ healthLabel }}</span>
        <button class="ke-button primary" @click="openCreateBase"><IconPlus />新建知识库</button>
      </div>
    </header>

    <div v-if="!capabilities.loaded" class="ke-loading-page">
      <span class="ke-spinner"></span><p>正在连接知识服务…</p>
    </div>
    <div v-else-if="!capabilities.enabled" class="ke-empty-page">
      <div class="ke-empty-illustration"><IconLibrary /></div>
      <h2>知识服务尚未启用</h2>
      <p>主聊天仍可正常使用。请由管理员完成知识服务配置后再试。</p>
    </div>

    <div v-else class="ke-workspace" :class="{ 'ask-closed': !askOpen }" :style="knowledgeLayoutStyle">
      <aside class="ke-library-panel">
        <div class="ke-library-heading">
          <div><strong>我的知识库</strong><small>{{ bases.length }} 个知识库</small></div>
          <button class="ke-icon-button" aria-label="新建知识库" title="新建知识库" @click="openCreateBase"><IconPlus /></button>
        </div>

        <label class="ke-search-box sidebar-search">
          <IconSearch />
          <input v-model="baseQuery" placeholder="搜索知识库" />
          <button v-if="baseQuery" aria-label="清空搜索" @click="baseQuery = ''"><IconX /></button>
        </label>

        <nav class="ke-space-list" aria-label="知识库空间">
          <section v-for="space in spaces" :key="space.value" class="ke-space-group" :class="{ active: selectedSpace === space.value }">
            <button class="ke-space-trigger" @click="selectSpace(space.value)">
              <component :is="space.icon" />
              <span><strong>{{ space.label }}</strong><small>{{ space.description }}</small></span>
              <b>{{ baseCount(space.value) }}</b>
              <IconChevronDown :class="{ rotated: selectedSpace === space.value }" />
            </button>
            <div v-if="selectedSpace === space.value" class="ke-space-bases">
              <button
                v-for="base in basesForSpace(space.value)"
                :key="base.id"
                class="ke-base-card"
                :class="{ active: selectedBaseId === base.id }"
                @click="selectBase(base.id)"
              >
                <span class="ke-base-cover" :class="base.visibility"><IconBookOpen /></span>
                <span class="ke-base-copy"><strong>{{ base.name }}</strong><small>{{ base.document_count }} 篇资料 · {{ roleLabel(base.role) }}</small></span>
                <IconChevronRight class="ke-chevron" />
              </button>
              <div v-if="!basesForSpace(space.value).length" class="ke-space-empty">
                {{ baseQuery ? '没有匹配的知识库' : '暂无知识库' }}
                <button v-if="!baseQuery && canCreateInSpace(space.value)" @click="openCreateBase"><IconPlus />创建</button>
                <small v-else-if="!baseQuery && space.value === 'team'">{{ teamSpaceEmptyHint(teamSpaceManagement) }}</small>
              </div>
            </div>
          </section>
        </nav>

        <div class="ke-library-tip">
          <IconShieldCheck />
          <span>访问权限会在每次检索时重新校验</span>
        </div>
      </aside>

      <div
        class="ke-resizer library"
        role="separator"
        aria-label="调整知识库导航宽度"
        aria-orientation="vertical"
        :aria-valuenow="libraryWidth"
        tabindex="0"
        @pointerdown="startKnowledgeResize('library', $event)"
        @keydown="resizeKnowledgeWithKeyboard('library', $event)"
      ></div>

      <main class="ke-content-panel" @dragenter.prevent="dragging = true" @dragover.prevent @dragleave.self="dragging = false" @drop.prevent="handleDrop">
        <div v-if="dragging && selectedBase && can('upload')" class="ke-drop-mask">
          <div><IconUploadCloud /><strong>松开以上传资料</strong><span>文件将添加到当前知识库</span></div>
        </div>

        <div v-if="!selectedBase" class="ke-empty-page compact">
          <div class="ke-empty-illustration"><IconBookOpen /></div>
          <h2>选择一个知识库开始</h2>
          <p>在左侧选择已有知识库，或创建一个新的知识空间。</p>
          <button class="ke-button primary" @click="openCreateBase"><IconPlus />新建知识库</button>
        </div>

        <template v-else>
          <section class="ke-base-hero">
            <div class="ke-base-identity">
              <span class="ke-hero-cover" :class="selectedBase.visibility"><IconBookOpen /></span>
              <div>
                <div class="ke-title-row">
                  <h2>{{ selectedBase.name }}</h2>
                  <span class="ke-role">{{ roleLabel(selectedBase.role) }}</span>
                </div>
                <p>{{ selectedBase.description || '这是一个还没有填写简介的知识库。' }}</p>
                <div class="ke-base-meta">
                  <span><IconFiles />{{ selectedBase.document_count }} 篇资料</span>
                  <span><IconUsers />{{ spaceLabel(selectedBase.visibility) }}</span>
                  <span><IconClock3 />更新于 {{ formatDate(selectedBase.updated_at) }}</span>
                </div>
              </div>
            </div>
            <div class="ke-inline-actions">
              <button v-if="selectedBase.visibility !== 'personal' && can('manage_permissions')" class="ke-button quiet" @click="openPermissions"><IconShieldCheck />权限管理</button>
              <button v-if="can('edit_base')" class="ke-button quiet icon-only" title="编辑知识库" @click="editBase"><IconSettings2 /></button>
              <button v-if="can('delete_base')" class="ke-button quiet icon-only danger-hover" title="删除知识库" @click="removeBase"><IconTrash2 /></button>
            </div>
          </section>

          <section class="ke-content-toolbar">
            <div class="ke-folder-breadcrumbs">
              <button class="ke-folder-back" :disabled="!selectedFolderId" title="返回上一级" @click="goParentFolder"><IconArrowLeft /></button>
              <button @click="selectFolder('')">内容</button>
              <span v-for="folder in folderBreadcrumbs" :key="folder.id" class="ke-folder-crumb">
                <IconChevronRight />
                <button :class="{ current: folder.id === selectedFolderId }" @click="selectFolder(folder.id)">{{ folder.name }}</button>
              </span>
              <div class="ke-folder-actions">
                <button v-if="can('create_folder')" title="在当前位置新建文件夹" @click="addFolder"><IconFolderPlus />新建文件夹</button>
                <button v-if="selectedFolderId && can('edit_folder')" title="重命名当前文件夹" @click="renameCurrentFolder"><IconPencil /></button>
                <button v-if="selectedFolderId && can('delete_folder')" class="danger-hover" title="删除当前空文件夹" @click="removeFolder"><IconTrash2 /></button>
              </div>
            </div>

            <div class="ke-tool-row">
              <label class="ke-search-box">
                <IconSearch />
                <input v-model="search" placeholder="搜索当前知识库" @keyup.enter="loadDocuments" />
                <button v-if="search" aria-label="清空搜索" @click="search = ''; loadDocuments()"><IconX /></button>
              </label>
              <label class="ke-select-wrap">
                <IconListFilter />
                <select v-model="statusFilter" @change="loadDocuments">
                  <option value="">全部状态</option>
                  <option v-for="opt in statusOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                </select>
              </label>
              <div class="ke-view-switch" aria-label="资料视图">
                <button :class="{ active: viewMode === 'grid' }" title="卡片视图" @click="viewMode = 'grid'"><IconLayoutGrid /></button>
                <button :class="{ active: viewMode === 'list' }" title="列表视图" @click="viewMode = 'list'"><IconList /></button>
              </div>
              <div v-if="can('upload')" class="ke-import-wrap">
                <button type="button" class="ke-button primary upload" title="选择文件或文件夹导入">
                  <IconUpload />导入资料<IconChevronDown class="ke-import-caret" />
                </button>
                <div class="ke-import-menu">
                  <label class="ke-import-option"><IconFileText />选择文件<input type="file" multiple hidden @change="handleFiles" /></label>
                  <label class="ke-import-option"><IconFolder />选择文件夹（含子目录）<input type="file" webkitdirectory hidden @change="handleFolderFiles" /></label>
                </div>
              </div>
            </div>
          </section>

          <section class="ke-documents" :class="viewMode">
            <div v-if="currentFolders.length && !search && !statusFilter" class="ke-folder-grid">
              <button v-for="folder in currentFolders" :key="folder.id" class="ke-folder-card" @dblclick="selectFolder(folder.id)" @click="selectFolder(folder.id)">
                <span class="ke-folder-art"><IconFolder /></span>
                <span><strong>{{ folder.name }}</strong><small>{{ folder.child_count + folder.document_count }} 项 · {{ formatDate(folder.updated_at) }}更新</small></span>
                <IconChevronRight />
              </button>
            </div>

            <div v-if="loadingDocuments && !documents.length" class="ke-document-loading">
              <p>文档加载中<span v-for="index in 3" :key="index" class="ke-dot"></span></p>
            </div>

            <template v-else-if="documents.length">
              <article v-for="doc in documents" :key="doc.id" class="ke-document-card">
                <button class="ke-doc-open" :disabled="!previewable(doc)" title="双击预览" @dblclick="previewable(doc) && previewDocument(doc)">
                  <span class="ke-file-icon"><FileIcon :filename="doc.name" :size="18" /></span>
                  <span class="ke-document-copy">
                    <span class="ke-doc-title">
                      <strong :title="doc.name">{{ doc.name }}</strong>
                      <small>
                        {{ formatSize(doc.size_bytes) }} · {{ formatDate(doc.updated_at) }}
                        <template v-if="doc.original_available"> · 原文已存</template>
                      </small>
                    </span>
                  </span>
                </button>
                <div class="ke-document-details">
                  <span class="ke-status" :class="doc.status"><i></i>{{ documentStatusText(doc) }}</span>
                  <span v-if="doc.status === 'processing'" class="ke-processing-age">{{ processingElapsed(doc) }}</span>
                </div>
                <p v-if="doc.error" class="ke-error-text">{{ doc.error.message }}</p>
                <div class="ke-row-actions">
                  <button v-if="previewable(doc)" title="预览" @click="previewDocument(doc)"><IconEye /><span>预览</span></button>
                  <button v-if="!doc.optimistic" title="下载" @click="downloadDocument(doc)"><IconDownload /><span>下载</span></button>
                  <button v-if="doc.allowed_actions.includes('move_document')" title="移动" @click="openMoveDocument(doc)"><IconFolder /><span>移动</span></button>
                  <button v-if="['failed', 'cancelled', 'unknown'].includes(doc.status) && doc.allowed_actions.includes('retry_document')" title="重试解析" @click="retryDoc(doc)"><IconRefreshCw /><span>重试</span></button>
                  <button v-if="['pending', 'processing'].includes(doc.status) && doc.allowed_actions.includes('retry_document')" title="暂停解析任务" @click="pauseDoc(doc)"><IconPause /><span>暂停</span></button>
                  <button v-if="doc.status === 'paused' && doc.allowed_actions.includes('retry_document')" title="恢复解析任务" @click="resumeDoc(doc)"><IconPlay /><span>恢复</span></button>
                  <button v-if="doc.allowed_actions.includes('delete_document')" class="danger-text" title="删除" @click="removeDocument(doc)"><IconTrash2 /><span>删除</span></button>
                </div>
              </article>
            </template>

            <div v-else-if="!currentFolders.length || search || statusFilter" class="ke-empty-documents">
              <div class="ke-empty-icon"><IconFiles /></div>
              <h3>{{ search || statusFilter ? '没有找到匹配资料' : '添加第一篇资料' }}</h3>
              <p>{{ search || statusFilter ? '换个关键词或筛选条件试试。' : '支持批量选择文件，也可以直接拖拽到这里。' }}</p>
              <div v-if="can('upload') && !search && !statusFilter" class="ke-import-wrap">
                <button type="button" class="ke-button primary upload" title="选择文件或文件夹导入"><IconUpload />导入资料<IconChevronDown class="ke-import-caret" /></button>
                <div class="ke-import-menu">
                  <label class="ke-import-option"><IconFileText />选择文件<input type="file" multiple hidden @change="handleFiles" /></label>
                  <label class="ke-import-option"><IconFolder />选择文件夹（含子目录）<input type="file" webkitdirectory hidden @change="handleFolderFiles" /></label>
                </div>
              </div>
              <button v-else-if="search || statusFilter" class="ke-button quiet" @click="clearFilters">清除筛选</button>
            </div>
          </section>
        </template>
      </main>

      <div
        v-if="askOpen"
        class="ke-resizer ask"
        role="separator"
        aria-label="调整知识问答宽度"
        aria-orientation="vertical"
        :aria-valuenow="askWidth"
        tabindex="0"
        @pointerdown="startKnowledgeResize('ask', $event)"
        @keydown="resizeKnowledgeWithKeyboard('ask', $event)"
      ></div>

      <aside v-if="askOpen" class="ke-ask-panel">
        <header class="ke-ask-header">
          <div class="ke-ai-avatar"><IconSparkles /></div>
          <div><strong>问问知识库</strong><small>{{ selectedBase ? `基于「${selectedBase.name}」` : '选择知识库后开始提问' }}</small></div>
          <button class="ke-icon-button subtle" :disabled="asking || !selectedBase" aria-label="新对话" title="开启新对话（删除当前问答记录）" @click="startNewChat"><IconMessageSquarePlus /></button>
          <button v-if="asking" class="ke-icon-button subtle ke-stop-button" aria-label="停止回答" title="停止回答" @click="stopQuestion('user')"><IconSquare /></button>
          <button class="ke-icon-button subtle" aria-label="收起问答" title="收起问答" @click="closeAskPanel"><IconPanelRightClose /></button>
        </header>

        <div class="ke-answer" ref="askBody">
          <div v-if="!agentMessage && !asking && !askHistory.length" class="ke-answer-welcome">
            <div class="ke-answer-orb"><IconSparkles /></div>
            <h3>{{ selectedBase ? '从资料里找答案' : '先选择一个知识库' }}</h3>
            <p>{{ selectedBase ? '我会基于当前知识库回答，并标明引用来源。' : '选择后即可对资料进行提问、总结和对比。' }}</p>
            <div v-if="selectedBase" class="ke-question-suggestions">
              <button v-for="item in questionSuggestions" :key="item" @click="submitQuestion(item)">{{ item }}<IconArrowUpRight /></button>
            </div>
          </div>
          <div v-for="(entry, index) in askHistory" :key="'ke-history-' + index" class="ke-history-entry">
            <div v-if="entry.question" class="ke-question-bubble">{{ entry.question }}</div>
            <template v-if="entry.message">
              <div class="ke-answer-label"><span class="ke-ai-avatar small"><IconSparkles /></span><strong>EasyAgent 知识库回答</strong></div>
              <div class="ke-agent-message"><ChatMessage :message="entry.message" /></div>
            </template>
          </div>
          <template v-if="agentMessage">
            <div class="ke-question-bubble">{{ lastQuestion }}</div>
            <div class="ke-answer-label"><span class="ke-ai-avatar small"><IconSparkles /></span><strong>EasyAgent 知识库回答</strong></div>
            <div class="ke-agent-message"><ChatMessage :message="agentMessage" /></div>
          </template>
          <div v-if="askError && !asking" class="ke-ask-error"><IconCircleAlert /><p>{{ askError }}</p><button @click="retryQuestion"><IconRefreshCw />重试</button></div>
        </div>

        <form class="ke-ask-form" @submit.prevent="submitQuestion()">
          <div class="ke-composer" :class="{ disabled: !selectedBase }">
            <textarea v-model="question" :disabled="!selectedBase || asking" rows="3" placeholder="基于这个知识库提问…" @keydown.enter.exact.prevent="submitQuestion()"></textarea>
            <div><span><IconDatabase />仅检索当前知识库</span><button :disabled="!question.trim() || !selectedBase || asking" aria-label="发送问题"><IconArrowUp /></button></div>
          </div>
          <small>回答由 AI 生成，请结合原始资料核验。</small>
        </form>
      </aside>

      <button v-else class="ke-ask-fab" @click="openAskPanel"><IconSparkles /><span>问问知识库</span></button>
    </div>

    <div v-if="showBaseModal" class="ke-modal-mask" @click.self="showBaseModal = false">
      <form class="ke-modal" @submit.prevent="saveBase">
        <header><div><h3>{{ baseModalMode === 'create' ? '新建知识库' : '知识库设置' }}</h3><p>{{ baseModalMode === 'create' ? '集中管理一组相关资料，并通过 AI 随时调用。' : '修改知识库的名称与说明。' }}</p></div><button type="button" aria-label="关闭" @click="showBaseModal = false"><IconX /></button></header>
        <label>名称<input v-model="baseForm.name" maxlength="127" placeholder="例如：宏观研究资料库" required /></label>
        <label>简介<textarea v-model="baseForm.description" maxlength="2000" placeholder="说明这个知识库包含什么内容，以及适合怎样使用。"></textarea></label>
        <label v-if="baseModalMode === 'create'">空间
          <select v-model="baseForm.visibility"><option value="personal">个人空间</option><option value="team" :disabled="!teamSpaceManagement.can_create">公共空间{{ teamSpaceOptionSuffix(teamSpaceManagement) }}</option></select>
          <small v-if="baseForm.visibility === 'team' && teamSpaceManagement.is_admin">admin 无需授权；请选择公共空间所属部门。</small>
          <small v-else-if="baseForm.visibility === 'team'">已授权管理员可创建和管理；本部门其他成员默认只读，可在权限管理中勾选限定可见人员。</small>
          <small v-else>空间决定知识库的默认可见范围，创建后可在权限管理中精细控制可见人员。</small>
        </label>
        <label v-if="baseModalMode === 'create' && baseForm.visibility === 'team' && teamSpaceManagement.is_admin">所属部门
          <select v-model="baseForm.department_id" required>
            <option disabled value="">请选择部门</option>
            <option v-for="department in teamSpaceManagement.departments" :key="department.id" :value="department.id">{{ department.name || department.id }}</option>
          </select>
          <small>所选部门的在职成员将默认获得查看权限。</small>
        </label>
        <div v-if="baseModalMode === 'create' && baseForm.visibility === 'team'" class="ke-create-perm">
          <label class="ke-create-perm-toggle">
            <input type="checkbox" v-model="createPermRestricted" />
            <span>限定可见人员</span>
            <small>开启后仅勾选人员可查看；不开启则所选部门在职成员默认可见。创建后可随时在权限管理中调整。</small>
          </label>
          <template v-if="createPermRestricted">
            <div v-if="createPermLoading" class="ke-perm-loading"><span class="ke-spinner"></span><p>正在加载人员目录…</p></div>
            <template v-else>
              <div class="ke-perm-toolbar">
                <span>已选 <strong>{{ createPermSelectedTotal }}</strong> / {{ createPermDirectoryTotal }} 人</span>
              </div>
              <div class="ke-perm-tree ke-create-perm-tree">
                <section v-for="dept in createPermTree" :key="dept.key" class="ke-perm-branch">
                  <div class="ke-perm-row level-dept">
                    <button type="button" class="ke-perm-caret" title="展开/收起" @click="toggleCreatePermExpand(dept.key)"><IconChevronDown :class="{ rotated: isCreatePermCollapsed(dept.key) }" /></button>
                    <input type="checkbox" v-indeterminate="permNodeIndeterminate(dept)" :checked="permNodeChecked(dept)" @change="togglePermNode(dept, $event.target.checked)" />
                    <span class="ke-perm-name">{{ dept.name }}</span>
                    <small class="ke-perm-count">{{ permSelectedCount(dept) }}/{{ dept.allUsers.length }}</small>
                  </div>
                  <div v-show="!isCreatePermCollapsed(dept.key)" class="ke-perm-children">
                    <label v-for="user in dept.users" :key="user.user_id" class="ke-perm-user">
                      <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                      <span>{{ user.display_name }}</span>
                      <small>{{ user.username }}</small>
                    </label>
                    <section v-for="division in dept.divisions" :key="division.key" class="ke-perm-branch">
                      <div class="ke-perm-row level-division">
                        <button type="button" class="ke-perm-caret" title="展开/收起" @click="toggleCreatePermExpand(division.key)"><IconChevronDown :class="{ rotated: isCreatePermCollapsed(division.key) }" /></button>
                        <input type="checkbox" v-indeterminate="permNodeIndeterminate(division)" :checked="permNodeChecked(division)" @change="togglePermNode(division, $event.target.checked)" />
                        <span class="ke-perm-name">{{ division.name }}</span>
                        <small class="ke-perm-count">{{ permSelectedCount(division) }}/{{ division.allUsers.length }}</small>
                      </div>
                      <div v-show="!isCreatePermCollapsed(division.key)" class="ke-perm-children">
                        <label v-for="user in division.users" :key="user.user_id" class="ke-perm-user">
                          <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                          <span>{{ user.display_name }}</span>
                          <small>{{ user.username }}</small>
                        </label>
                        <section v-for="team in division.teams" :key="team.key" class="ke-perm-branch">
                          <div class="ke-perm-row level-team">
                            <button type="button" class="ke-perm-caret" title="展开/收起" @click="toggleCreatePermExpand(team.key)"><IconChevronDown :class="{ rotated: isCreatePermCollapsed(team.key) }" /></button>
                            <input type="checkbox" v-indeterminate="permNodeIndeterminate(team)" :checked="permNodeChecked(team)" @change="togglePermNode(team, $event.target.checked)" />
                            <span class="ke-perm-name">{{ team.name }}</span>
                            <small class="ke-perm-count">{{ permSelectedCount(team) }}/{{ team.allUsers.length }}</small>
                          </div>
                          <div v-show="!isCreatePermCollapsed(team.key)" class="ke-perm-children">
                            <label v-for="user in team.users" :key="user.user_id" class="ke-perm-user">
                              <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                              <span>{{ user.display_name }}</span>
                              <small>{{ user.username }}</small>
                            </label>
                          </div>
                        </section>
                      </div>
                    </section>
                  </div>
                </section>
                <p v-if="!createPermTree.length" class="ke-perm-empty">暂无可选人员，请先在人员与部门中导入账号。</p>
              </div>
              <p class="ke-permission-note">
                <template v-if="createPermSelectedTotal > 0">创建后，该知识库仅勾选人员及所有者、空间管理员可见；未勾选人员（含本部门其他成员）不可见。</template>
                <template v-else>未勾选任何人员，创建后仅所有者与空间管理员可见。</template>
              </p>
            </template>
          </template>
        </div>
        <footer><button type="button" class="ke-button quiet" @click="showBaseModal = false">取消</button><button class="ke-button primary" :disabled="saving">{{ saving ? '保存中…' : baseModalMode === 'create' ? '创建知识库' : '保存修改' }}</button></footer>
      </form>
    </div>

    <div v-if="showFolderModal" class="ke-modal-mask" @click.self="showFolderModal = false">
      <form class="ke-modal small" @submit.prevent="saveFolder">
        <header><div><h3>{{ folderForm.id ? '重命名文件夹' : '新建文件夹' }}</h3><p>使用文件夹整理当前知识库中的资料。</p></div><button type="button" aria-label="关闭" @click="showFolderModal = false"><IconX /></button></header>
        <label>文件夹名称<input v-model="folderForm.name" maxlength="127" placeholder="输入文件夹名称" required autofocus /></label>
        <footer><button type="button" class="ke-button quiet" @click="showFolderModal = false">取消</button><button class="ke-button primary" :disabled="saving">保存</button></footer>
      </form>
    </div>

    <div v-if="showMoveModal" class="ke-modal-mask" @click.self="showMoveModal = false">
      <form class="ke-modal small" @submit.prevent="confirmMoveDocument">
        <header><div><h3>移动资料</h3><p>将「{{ movingDocument ? movingDocument.name : '' }}」放入指定文件夹。</p></div><button type="button" aria-label="关闭" @click="showMoveModal = false"><IconX /></button></header>
        <label>目标位置
          <select v-model="moveTargetFolderId">
            <option value="">内容（根目录）</option>
            <option v-for="folder in folderOptions" :key="folder.id" :value="folder.id">{{ folder.path }}</option>
          </select>
        </label>
        <footer><button type="button" class="ke-button quiet" @click="showMoveModal = false">取消</button><button class="ke-button primary" :disabled="saving">确认移动</button></footer>
      </form>
    </div>

    <div v-if="showPermissionModal" class="ke-modal-mask" @click.self="showPermissionModal = false">
      <div class="ke-modal wide">
        <header>
          <div>
            <h3>权限管理</h3>
            <p>按部门 &gt; 处室 &gt; 团队勾选可见人员；勾选目录级即全选该级别下所有人员。保存后，仅勾选人员可查看「{{ selectedBase ? selectedBase.name : '' }}」。</p>
          </div>
          <button aria-label="关闭" @click="showPermissionModal = false"><IconX /></button>
        </header>
        <div v-if="permissionDirectoryLoading" class="ke-perm-loading"><span class="ke-spinner"></span><p>正在加载人员目录…</p></div>
        <template v-else>
          <div class="ke-perm-toolbar">
            <span>已选 <strong>{{ permSelectedTotal }}</strong> / {{ permDirectoryTotal }} 人</span>
            <span v-if="permissionRestricted">当前已限定：仅勾选人员可见</span>
            <span v-else>当前：默认可见范围（公共空间对本部门成员可见）</span>
          </div>
          <div class="ke-perm-tree">
            <section v-for="dept in permissionTree" :key="dept.key" class="ke-perm-branch">
              <div class="ke-perm-row level-dept">
                <button type="button" class="ke-perm-caret" title="展开/收起" @click="togglePermExpand(dept.key)"><IconChevronDown :class="{ rotated: isPermCollapsed(dept.key) }" /></button>
                <input type="checkbox" v-indeterminate="permNodeIndeterminate(dept)" :checked="permNodeChecked(dept)" @change="togglePermNode(dept, $event.target.checked)" />
                <span class="ke-perm-name">{{ dept.name }}</span>
                <small class="ke-perm-count">{{ permSelectedCount(dept) }}/{{ dept.allUsers.length }}</small>
              </div>
              <div v-show="!isPermCollapsed(dept.key)" class="ke-perm-children">
                <label v-for="user in dept.users" :key="user.user_id" class="ke-perm-user">
                  <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                  <span>{{ user.display_name }}</span>
                  <small>{{ user.username }}</small>
                </label>
                <section v-for="division in dept.divisions" :key="division.key" class="ke-perm-branch">
                  <div class="ke-perm-row level-division">
                    <button type="button" class="ke-perm-caret" title="展开/收起" @click="togglePermExpand(division.key)"><IconChevronDown :class="{ rotated: isPermCollapsed(division.key) }" /></button>
                    <input type="checkbox" v-indeterminate="permNodeIndeterminate(division)" :checked="permNodeChecked(division)" @change="togglePermNode(division, $event.target.checked)" />
                    <span class="ke-perm-name">{{ division.name }}</span>
                    <small class="ke-perm-count">{{ permSelectedCount(division) }}/{{ division.allUsers.length }}</small>
                  </div>
                  <div v-show="!isPermCollapsed(division.key)" class="ke-perm-children">
                    <label v-for="user in division.users" :key="user.user_id" class="ke-perm-user">
                      <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                      <span>{{ user.display_name }}</span>
                      <small>{{ user.username }}</small>
                    </label>
                    <section v-for="team in division.teams" :key="team.key" class="ke-perm-branch">
                      <div class="ke-perm-row level-team">
                        <button type="button" class="ke-perm-caret" title="展开/收起" @click="togglePermExpand(team.key)"><IconChevronDown :class="{ rotated: isPermCollapsed(team.key) }" /></button>
                        <input type="checkbox" v-indeterminate="permNodeIndeterminate(team)" :checked="permNodeChecked(team)" @change="togglePermNode(team, $event.target.checked)" />
                        <span class="ke-perm-name">{{ team.name }}</span>
                        <small class="ke-perm-count">{{ permSelectedCount(team) }}/{{ team.allUsers.length }}</small>
                      </div>
                      <div v-show="!isPermCollapsed(team.key)" class="ke-perm-children">
                        <label v-for="user in team.users" :key="user.user_id" class="ke-perm-user">
                          <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                          <span>{{ user.display_name }}</span>
                          <small>{{ user.username }}</small>
                        </label>
                      </div>
                    </section>
                  </div>
                </section>
              </div>
            </section>
            <p v-if="!permissionTree.length" class="ke-perm-empty">暂无可选人员，请先在人员与部门中导入账号。</p>
          </div>
          <p class="ke-permission-note">
            <template v-if="permSelectedTotal > 0">保存后，该知识库仅勾选人员及所有者、空间管理员可见；未勾选人员（含本部门其他成员）不可见。</template>
            <template v-else>未勾选任何人员，保存后恢复默认可见范围（公共空间对本部门成员可见）。</template>
          </p>
        </template>
        <footer><button class="ke-button quiet" @click="showPermissionModal = false">取消</button><button class="ke-button primary" :disabled="saving || permissionDirectoryLoading" @click="savePermissions">{{ saving ? '保存中…' : '保存权限' }}</button></footer>
      </div>
    </div>

    <div v-if="confirmation.open" class="ke-modal-mask" @click.self="resolveConfirmation(false)">
      <div class="ke-modal confirm">
        <span class="ke-confirm-icon" :class="confirmation.tone"><IconTriangleAlert /></span>
        <h3>{{ confirmation.title }}</h3><p>{{ confirmation.description }}</p>
        <footer><button class="ke-button quiet" @click="resolveConfirmation(false)">取消</button><button class="ke-button danger" @click="resolveConfirmation(true)">{{ confirmation.confirmLabel }}</button></footer>
      </div>
    </div>

    <div v-if="preview.open" class="ke-modal-mask preview-mask" @click.self="closePreview">
      <div class="ke-preview-modal">
        <header><div><FileIcon :filename="preview.name" :size="22" /><h3>{{ preview.name }}</h3></div><button aria-label="关闭预览" @click="closePreview"><IconX /></button></header>
        <div v-if="preview.loading" class="ke-preview-loading"><span class="ke-spinner"></span><p>正在加载文件预览…</p></div>
        <iframe v-else-if="preview.kind === 'frame'" :src="preview.url" title="文档预览"></iframe>
        <DocxPreview v-else-if="preview.kind === 'docx'" :file-url="preview.url" />
        <ExcelPreview v-else-if="preview.kind === 'excel'" :file-url="preview.url" />
        <div v-else-if="preview.kind === 'pptx'" class="ke-preview-unsupported">当前环境暂不支持 PPTX 在线渲染，请使用下载按钮查看。</div>
        <img v-else-if="preview.kind === 'image'" :src="preview.url" :alt="preview.name" />
        <pre v-else>{{ preview.text }}</pre>
      </div>
    </div>

    <Transition name="ke-toast"><div v-if="toast" class="ke-toast" :class="toast.type"><IconCircleCheck v-if="toast.type !== 'error'" /><IconCircleAlert v-else />{{ toast.message }}</div></Transition>
  </section>
</template>

<script>
import { computed, getCurrentInstance, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
// 图标统一走 ./icons.js（vue2 构建栈为 unplugin-icons raw 编译 + 函数式组件包装），
// 组件用法 <IconX /> 与 frontend/ 直接导入 ~icons 的模板写法保持一致。
import {
  IconArrowLeft,
  IconArrowUp,
  IconArrowUpRight,
  IconBookOpen,
  IconChevronDown,
  IconChevronRight,
  IconCircleAlert,
  IconCircleCheck,
  IconClock3,
  IconDatabase,
  IconDownload,
  IconEye,
  IconFiles,
  IconFileText,
  IconFolder,
  IconFolderPlus,
  IconLayoutGrid,
  IconLibrary,
  IconList,
  IconListFilter,
  IconMessageSquarePlus,
  IconPanelRightClose,
  IconPause,
  IconPencil,
  IconPlay,
  IconPlus,
  IconRefreshCw,
  IconSearch,
  IconSettings2,
  IconShieldCheck,
  IconSparkles,
  IconSquare,
  IconTrash2,
  IconTriangleAlert,
  IconUpload,
  IconUploadCloud,
  IconUserRound,
  IconUserRoundCog,
  IconUsers,
  IconX,
} from './icons.js'
import DocxPreview from '../../shared/preview/DocxPreview.vue'
import ExcelPreview from '../../shared/preview/ExcelPreview.vue'
import FileIcon from '../../shared/components/FileIcon.vue'
import ChatMessage from '../chat/components/ChatMessage.vue'
import { attachStream, getChatHistory, sendMessage } from '../../api/chat.js'
import { cancelMessage } from './api.js'
import { processingElapsedText } from './utils/presentation.js'
import {
  defaultTeamDepartmentId,
  teamSpaceEmptyHint,
  teamSpaceOptionSuffix,
} from './utils/teamSpace.js'
import {
  createOptimisticUploadDocument,
  mergeServerDocumentsWithOptimistic,
} from './utils/uploadPresentation.js'
import {
  createFolder, createKnowledgeBase, createKnowledgeChatRound, deleteDocument, deleteFolder,
  deleteKnowledgeBase, ensureFolders, getDocumentBlob, getKnowledgeCapabilities, getKnowledgeStatus,
  listCreationPermissionDirectory, listDocuments, listFolders, listKnowledgeBases, listKnowledgeOperations, listPermissionDirectory, listPermissions,
  moveDocument, pauseDocument, prepareKnowledgeChatSession, replacePermissions, resumeDocument, retryDocument, updateFolder,
  updateKnowledgeBase, uploadDocument,
} from './api.js'

export default {
  components: {
    ChatMessage,
    DocxPreview,
    ExcelPreview,
    FileIcon,
    IconArrowLeft,
    IconArrowUp,
    IconArrowUpRight,
    IconBookOpen,
    IconChevronDown,
    IconChevronRight,
    IconCircleAlert,
    IconCircleCheck,
    IconClock3,
    IconDatabase,
    IconDownload,
    IconEye,
    IconFiles,
    IconFileText,
    IconFolder,
    IconFolderPlus,
    IconLayoutGrid,
    IconLibrary,
    IconList,
    IconListFilter,
    IconMessageSquarePlus,
    IconPanelRightClose,
    IconPause,
    IconPencil,
    IconPlay,
    IconPlus,
    IconRefreshCw,
    IconSearch,
    IconSettings2,
    IconShieldCheck,
    IconSparkles,
    IconSquare,
    IconTrash2,
    IconTriangleAlert,
    IconUpload,
    IconUploadCloud,
    IconUserRound,
    IconUserRoundCog,
    IconUsers,
    IconX,
  },
  emits: ['close', 'manage-personnel', 'chat-session-prepared'],
  // Vue 2 模板无法直接绑定 checkbox 的 indeterminate 属性，用指令补齐
  directives: {
    indeterminate: {
      bind(el, binding) { el.indeterminate = Boolean(binding.value) },
      update(el, binding) { el.indeterminate = Boolean(binding.value) },
    },
  },
  setup(_props, { emit }) {

const spaces = [
  { value: 'personal', label: '个人空间', description: '仅自己可见', icon: IconUserRound },
  { value: 'team', label: '公共空间', description: '本部门成员可见', icon: IconUsers },
]
const questionSuggestions = ['概括这批资料的核心结论', '提取重要数据与时间点', '不同报告有哪些观点分歧？']
// Vue 2.7 对 setup 返回的对象 ref 替换存在边界差异，固定对象身份更稳定。
const capabilities = reactive({ loaded: false, enabled: false })
const teamSpaceManagement = reactive({ can_create: false, can_manage: false, department_id: null, is_admin: false, departments: [] })
const health = ref(null)
const bases = ref([])
const baseQuery = ref('')
const selectedSpace = ref('personal')
const selectedBaseId = ref('')
const folders = ref([])
const selectedFolderId = ref('')
const documents = ref([])
const documentsPage = ref({ total: 0 })
const loadingDocuments = ref(false)
const search = ref('')
const statusFilter = ref('')
// 状态筛选下拉：仅显示当前范围内文档实际存在的状态（非筛选态时从全量文档收集）
const STATUS_META = [
  ['pending', '待处理'], ['processing', '解析中'], ['cancel_requested', '取消中'],
  ['cancelled', '已取消'], ['paused', '已暂停'], ['ready', '已就绪'],
  ['failed', '失败'], ['deleting', '删除中'], ['unknown', '未知'],
]
const STATUS_LABELS = Object.fromEntries(STATUS_META)
const availableStatuses = ref([])
const statusOptions = computed(() => {
  const current = statusFilter.value
  const list = availableStatuses.value
    .filter(s => STATUS_LABELS[s])
    .sort((a, b) => STATUS_META.findIndex(m => m[0] === a) - STATUS_META.findIndex(m => m[0] === b))
    .map(s => ({ value: s, label: STATUS_LABELS[s] }))
  // 当前选中的状态即使已不在集合中（如最后一份失败文档状态流转）也保留，避免下拉显示空白
  if (current && STATUS_LABELS[current] && !list.some(o => o.value === current)) {
    list.push({ value: current, label: STATUS_LABELS[current] })
  }
  return list
})
const viewMode = ref(localStorage.getItem('ke-view-mode') || 'list')
const dragging = ref(false)
const uploadQueue = ref([])
const operations = ref([])
const askOpen = ref(true)
// 窄容器（≤1100px，与下方 container query 断点一致）下问答面板是 absolute 浮层，
// 会盖住文档列表的文档名；默认收起为悬浮按钮，用户点开后才以浮层展示。
// 拖动外层会话栏宽度导致容器跨入窄断点时同样自动收起（用户刚手动展开的除外）。
const ASK_FLOATING_BREAKPOINT = 1100
let askUserOpened = false
let shellWidthObserver = null
function openAskPanel() { askUserOpened = true; askOpen.value = true }
function closeAskPanel() { askUserOpened = false; askOpen.value = false }
function watchShellWidth() {
  // 组件根元素即 .ke-shell（Options API setup 返回的 ref 不绑定模板 ref，故走 $el）
  const shell = getCurrentInstance()?.proxy?.$el
  if (!shell || typeof ResizeObserver === 'undefined') return
  if (shell.clientWidth <= ASK_FLOATING_BREAKPOINT) askOpen.value = false
  shellWidthObserver = new ResizeObserver(() => {
    if (shell.clientWidth <= ASK_FLOATING_BREAKPOINT && askOpen.value && !askUserOpened) askOpen.value = false
  })
  shellWidthObserver.observe(shell)
}
function stopShellWidthWatch() {
  if (shellWidthObserver) { shellWidthObserver.disconnect(); shellWidthObserver = null }
}
const clampLayout = (value, min, max) => Math.min(max, Math.max(min, value))
const storedLayoutNumber = (key, fallback) => {
  const value = Number(localStorage.getItem(key))
  return Number.isFinite(value) ? value : fallback
}
const libraryWidth = ref(clampLayout(storedLayoutNumber('easyagent.layout.knowledge.library', 252), 200, 420))
const askWidth = ref(clampLayout(storedLayoutNumber('easyagent.layout.knowledge.ask', 344), 280, 560))
const knowledgeLayoutStyle = computed(() => ({
  '--ke-library-width': `${libraryWidth.value}px`,
  '--ke-ask-width': `${askWidth.value}px`,
}))
let activeKnowledgeResize = null

function finishKnowledgeResize() {
  if (!activeKnowledgeResize) return
  localStorage.setItem('easyagent.layout.knowledge.library', String(libraryWidth.value))
  localStorage.setItem('easyagent.layout.knowledge.ask', String(askWidth.value))
  activeKnowledgeResize = null
  document.body.classList.remove('ke-layout-resizing')
  window.removeEventListener('pointermove', handleKnowledgeResize)
  window.removeEventListener('pointerup', finishKnowledgeResize)
  window.removeEventListener('pointercancel', finishKnowledgeResize)
}

function handleKnowledgeResize(event) {
  if (!activeKnowledgeResize) return
  const delta = event.clientX - activeKnowledgeResize.startX
  if (activeKnowledgeResize.kind === 'library') {
    libraryWidth.value = clampLayout(activeKnowledgeResize.startWidth + delta, 200, 420)
  } else {
    askWidth.value = clampLayout(activeKnowledgeResize.startWidth - delta, 280, 560)
  }
}

function startKnowledgeResize(kind, event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  activeKnowledgeResize = {
    kind,
    startX: event.clientX,
    startWidth: kind === 'library' ? libraryWidth.value : askWidth.value,
  }
  document.body.classList.add('ke-layout-resizing')
  window.addEventListener('pointermove', handleKnowledgeResize)
  window.addEventListener('pointerup', finishKnowledgeResize)
  window.addEventListener('pointercancel', finishKnowledgeResize)
  event.preventDefault()
}

function resizeKnowledgeWithKeyboard(kind, event) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  const direction = event.key === 'ArrowRight' ? 1 : -1
  if (kind === 'library') {
    libraryWidth.value = clampLayout(libraryWidth.value + direction * 16, 200, 420)
    localStorage.setItem('easyagent.layout.knowledge.library', String(libraryWidth.value))
  } else {
    askWidth.value = clampLayout(askWidth.value - direction * 16, 280, 560)
    localStorage.setItem('easyagent.layout.knowledge.ask', String(askWidth.value))
  }
  event.preventDefault()
}
const question = ref('')
const lastQuestion = ref('')
const agentMessage = ref(null)
// 面板内已完成的历史问答：[{question, message}]，多轮问答均可回看（含各自的知识引用）
const askHistory = ref([])
// 已拉取过问答历史的知识库 ID（含历史为空的库），避免重复请求；切换知识库时重置
const askHistoryBaseId = ref('')
let askHistoryFetching = false
// 「开启新对话」重置计数：作废在途的历史拉取，防止已删除的旧历史复活
let askHistoryResetCount = 0
const askBody = ref(null)
const asking = ref(false)
const askError = ref('')
let askController = null
let askGeneration = 0
let activeChatSessionId = ''
let streamDone = false
let blockOrder = 0
const showBaseModal = ref(false)
const baseModalMode = ref('create')
const showFolderModal = ref(false)
const showMoveModal = ref(false)
const movingDocument = ref(null)
const moveTargetFolderId = ref('')
const showPermissionModal = ref(false)
const saving = ref(false)
const baseForm = reactive({ name: '', description: '', visibility: 'personal', department_id: '' })
const folderForm = reactive({ id: '', name: '', parentId: '' })
const confirmation = reactive({ open: false, title: '', description: '', confirmLabel: '确认删除', tone: 'danger' })
// 权限管理：人员目录（含 checked 勾选态）、三级树、展开态与受限标记
const permissionDirectory = ref([])
const permissionTree = ref([])
const permissionDirectoryLoading = ref(false)
const permissionRestricted = ref(false)
const permissionCollapsed = ref({})
let permissionLoadSeq = 0
// 创建知识库时的可见人员配置（与权限管理同款三级树，独立状态避免串扰）
const createPermDirectory = ref([])
const createPermTree = ref([])
const createPermLoading = ref(false)
const createPermRestricted = ref(false)
const createPermCollapsed = ref({})
let createPermLoadSeq = 0
const toast = ref(null)
const preview = reactive({ open: false, loading: false, name: '', kind: '', url: '', text: '', buffer: null })
let pollTimer = null
let toastTimer = null
let confirmResolver = null
let documentRequestSequence = 0

const selectedBase = computed(() => bases.value.find(item => item.id === selectedBaseId.value) || null)
const currentFolders = computed(() => folders.value.filter(item => (item.parent_id || '') === selectedFolderId.value))
const currentFolder = computed(() => folders.value.find(item => item.id === selectedFolderId.value) || null)
const folderBreadcrumbs = computed(() => {
  const chain = []
  const visited = new Set()
  let cursor = currentFolder.value
  while (cursor && !visited.has(cursor.id)) {
    visited.add(cursor.id)
    chain.unshift(cursor)
    cursor = folders.value.find(item => item.id === cursor.parent_id)
  }
  return chain
})
const folderOptions = computed(() => folders.value.map(folder => ({ ...folder, path: folderPath(folder) })).sort((a, b) => a.path.localeCompare(b.path, 'zh-CN')))
const healthClass = computed(() => health.value?.ready ? 'ready' : 'degraded')
const healthLabel = computed(() => health.value?.ready ? '知识服务正常' : '知识服务待就绪')
const hasActiveWork = computed(() => documents.value.some(
  item => !item.optimistic && ['pending', 'processing', 'cancel_requested', 'deleting'].includes(item.status),
))
const hasCurrentUploadRequest = computed(() => uploadQueue.value.some(
  item => item.baseId === selectedBaseId.value && ['uploading', 'saving'].includes(item.state),
))
const permSelectedTotal = computed(() => permissionDirectory.value.filter(user => user.checked).length)
const permDirectoryTotal = computed(() => permissionDirectory.value.length)
const createPermSelectedTotal = computed(() => createPermDirectory.value.filter(user => user.checked).length)
const createPermDirectoryTotal = computed(() => createPermDirectory.value.length)

watch(hasActiveWork, active => active ? startPolling() : stopPolling())
watch(viewMode, value => localStorage.setItem('ke-view-mode', value))
function can(action) { return Boolean(selectedBase.value?.allowed_actions?.includes(action)) }
function canCreateInSpace(space) { return space !== 'team' || teamSpaceManagement.can_create }
function baseCount(space) { return bases.value.filter(item => item.visibility === space).length }
function basesForSpace(space) {
  const keyword = baseQuery.value.trim().toLowerCase()
  return bases.value.filter(item => item.visibility === space && (!keyword || item.name.toLowerCase().includes(keyword) || (item.description || '').toLowerCase().includes(keyword)))
}
function notify(message, type = 'success') { toast.value = { message, type }; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.value = null }, 3000) }
function handleError(error) { console.error(error); notify(error?.message || '操作失败', 'error') }

async function initialize() {
  try {
    Object.assign(capabilities, await getKnowledgeCapabilities(), { loaded: true })
    if (!capabilities.enabled) return
    await Promise.all([loadBases(), loadHealth(), loadOperations()])
  } catch (error) { capabilities.loaded = true; handleError(error) }
}
async function loadHealth() { try { health.value = await getKnowledgeStatus() } catch (_) { health.value = { ready: false } } }
async function loadBases(preferredId = '') {
  const response = await listKnowledgeBases({ pageSize: 100 })
  bases.value = response.items || []
  Object.assign(
    teamSpaceManagement,
    { can_create: false, can_manage: false, department_id: null, is_admin: false, departments: [] },
    response.team_space_management || {}
  )
  const target = preferredId || selectedBaseId.value
  if (target && bases.value.some(item => item.id === target)) await selectBase(target)
  else if (basesForSpace(selectedSpace.value).length) await selectBase(basesForSpace(selectedSpace.value)[0].id)
  else { selectedBaseId.value = ''; folders.value = []; documents.value = [] }
}
async function refreshBaseCounts() {
  // 仅刷新左侧列表数据（document_count 等），不触发 selectBase 的选中/视图副作用
  try {
    const response = await listKnowledgeBases({ pageSize: 100 })
    bases.value = response.items || []
    Object.assign(
      teamSpaceManagement,
      { can_create: false, can_manage: false, department_id: null, is_admin: false, departments: [] },
      response.team_space_management || {}
    )
  } catch (_) { /* 数量刷新失败不影响主流程 */ }
}
async function selectSpace(space) {
  selectedSpace.value = space
  if (selectedBase.value?.visibility === space) return
  const first = basesForSpace(space)[0]
  if (first) await selectBase(first.id)
  else { selectedBaseId.value = ''; selectedFolderId.value = ''; folders.value = []; documents.value = [] }
}
async function selectBase(baseId) {
  if (asking.value && selectedBaseId.value !== baseId) stopQuestion('switch')
  if (selectedBaseId.value !== baseId) { documents.value = []; folders.value = [] }
  selectedBaseId.value = baseId; selectedFolderId.value = ''; agentMessage.value = null; askError.value = ''; askHistory.value = []; askHistoryBaseId.value = ''; lastQuestion.value = ''
  await Promise.all([loadFolders(), loadDocuments({ initial: true }), loadStatusOptions()])
  await loadOperations()
}
// 状态筛选选项：整个知识库实际存在的文档状态（不随当前文件夹/搜索窄化）
async function loadStatusOptions() {
  const baseId = selectedBaseId.value
  if (!baseId) { availableStatuses.value = []; return }
  try {
    const response = await listDocuments(baseId, { pageSize: 100 })
    if (selectedBaseId.value === baseId) {
      availableStatuses.value = [...new Set((response.items || []).map(d => d.status).filter(Boolean))]
    }
  } catch { /* 静默失败：下拉保留现有选项 */ }
}
async function selectFolder(folderId) { selectedFolderId.value = folderId; await loadDocuments() }
async function goParentFolder() { await selectFolder(currentFolder.value?.parent_id || '') }
async function loadFolders() {
  const baseId = selectedBaseId.value
  if (!baseId) return
  try {
    const response = await listFolders(baseId)
    if (selectedBaseId.value === baseId) folders.value = response.items || []
  } catch (error) { handleError(error) }
}
async function loadDocuments({ initial = false } = {}) {
  const baseId = selectedBaseId.value
  const folderId = selectedFolderId.value
  if (!baseId) return
  const requestSequence = ++documentRequestSequence
  loadingDocuments.value = initial || documents.value.length === 0
  try {
    const query = search.value.trim()
    const response = await listDocuments(baseId, { pageSize: 100, folderId, directOnly: !query, status: statusFilter.value, q: query })
    if (requestSequence !== documentRequestSequence || selectedBaseId.value !== baseId || selectedFolderId.value !== folderId) return
    const serverDocuments = response.items || []
    documents.value = mergeServerDocumentsWithOptimistic(
      serverDocuments,
      documents.value,
      {
        baseId,
        folderId,
        preserve: !query && !statusFilter.value,
      }
    )
    documentsPage.value = response.page || { total: serverDocuments.length }
    await loadFolders()
  } catch (error) { handleError(error) } finally {
    if (requestSequence === documentRequestSequence) loadingDocuments.value = false
  }
}
function clearFilters() { search.value = ''; statusFilter.value = ''; loadDocuments() }

function openCreateBase() {
  const visibility = canCreateInSpace(selectedSpace.value) ? selectedSpace.value : 'personal'
  Object.assign(baseForm, {
    name: '',
    description: '',
    visibility,
    department_id: defaultTeamDepartmentId(teamSpaceManagement),
  })
  resetCreatePermissions()
  if (visibility === 'team') ensureCreatePermDirectory()
  baseModalMode.value = 'create'
  showBaseModal.value = true
}
function editBase() { Object.assign(baseForm, { name: selectedBase.value.name, description: selectedBase.value.description || '', visibility: selectedBase.value.visibility, department_id: '' }); baseModalMode.value = 'edit'; showBaseModal.value = true }
async function saveBase() {
  saving.value = true
  try {
    if (baseModalMode.value === 'create') {
      const payload = {
        name: baseForm.name.trim(),
        description: baseForm.description || '',
        visibility: baseForm.visibility,
      }
      if (baseForm.visibility === 'team') {
        payload.department_id = baseForm.department_id || null
        // 创建即配置可见性：开启名单或勾选了人员即进入受限模式（仅勾选人员可见）
        if (createPermRestricted.value || createPermSelectedTotal.value > 0) {
          payload.restrict_access = true
          payload.permission_user_ids = createPermDirectory.value
            .filter(user => user.checked)
            .map(user => user.user_id)
        }
      }
      const created = await createKnowledgeBase(payload)
      selectedSpace.value = created.visibility
      await loadBases(created.id)
      notify('知识库已创建')
    } else {
      await updateKnowledgeBase(selectedBaseId.value, { name: baseForm.name.trim(), description: baseForm.description || '' })
      await loadBases(selectedBaseId.value)
      notify('知识库已更新')
    }
    showBaseModal.value = false
  } catch (error) { handleError(error) } finally { saving.value = false }
}
async function removeBase() {
  const confirmed = await askConfirmation({ title: '删除这个知识库？', description: `「${selectedBase.value.name}」及其中全部资料将被删除，此操作无法撤销。`, confirmLabel: '删除知识库' })
  if (!confirmed) return
  try { await deleteKnowledgeBase(selectedBaseId.value); selectedBaseId.value = ''; await loadBases(); notify('知识库已删除') } catch (error) { handleError(error) }
}

function addFolder() { Object.assign(folderForm, { id: '', name: '', parentId: selectedFolderId.value }); showFolderModal.value = true }
function renameCurrentFolder() { const folder = currentFolder.value; if (!folder) return; Object.assign(folderForm, { id: folder.id, name: folder.name, parentId: folder.parent_id || '' }); showFolderModal.value = true }
async function saveFolder() {
  if (!folderForm.name.trim()) return
  saving.value = true
  try {
    if (folderForm.id) { await updateFolder(folderForm.id, folderForm.name.trim()); notify('文件夹已重命名') }
    else { await createFolder(selectedBaseId.value, folderForm.name.trim(), folderForm.parentId); notify('文件夹已创建') }
    showFolderModal.value = false
    await loadFolders()
  } catch (error) { handleError(error) } finally { saving.value = false }
}
async function removeFolder() {
  const folder = folders.value.find(item => item.id === selectedFolderId.value)
  if (!folder || !await askConfirmation({ title: '删除这个文件夹？', description: `仅空文件夹「${folder.name}」可以被删除。`, confirmLabel: '删除文件夹' })) return
  try { const parentId = folder.parent_id || ''; await deleteFolder(folder.id); selectedFolderId.value = parentId; await Promise.all([loadFolders(), loadDocuments()]); notify('文件夹已删除') } catch (error) { handleError(error) }
}

async function handleFiles(event) { const files = Array.from(event.target.files || []); event.target.value = ''; await uploadFiles(files.map(file => ({ file, relativePath: '' }))) }
async function handleFolderFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  const picked = files.filter(isFolderImportableFile)
  const skipped = files.length - picked.length
  if (skipped > 0) notify(`已跳过 ${skipped} 个隐藏或临时文件`)
  if (!picked.length) return
  await uploadFiles(picked.map(file => ({ file, relativePath: file.webkitRelativePath || '' })))
}
function isFolderImportableFile(file) {
  const relativePath = file.webkitRelativePath || ''
  const segments = relativePath.split('/').filter(Boolean)
  if (segments.some(part => part.startsWith('.') || part === '__MACOSX' || part === 'node_modules')) return false
  if (file.name.startsWith('~$') || file.name === 'Thumbs.db' || file.name === 'desktop.ini') return false
  return true
}
function isHiddenDirSegment(segment) { return segment.startsWith('.') || segment === '__MACOSX' || segment === 'node_modules' }
function isJunkFileName(name) { return name.startsWith('.') || name.startsWith('~$') || name === 'Thumbs.db' || name === 'desktop.ini' }
async function collectDroppedEntry(entry, parentPath, result) {
  // 递归展开拖入的目录树：文件带完整相对路径，目录全量收集（含空目录）
  if (entry.isFile) {
    const dirSegments = parentPath ? parentPath.split('/').filter(Boolean) : []
    if (dirSegments.some(isHiddenDirSegment) || isJunkFileName(entry.name)) { result.skipped += 1; return }
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
    result.files.push({ file, relativePath: parentPath + entry.name })
    let cursor = parentPath
    while (cursor) {
      result.nonEmpty.add(cursor.replace(/\/+$/, ''))
      const slash = cursor.slice(0, -1).lastIndexOf('/')
      cursor = slash >= 0 ? cursor.slice(0, slash + 1) : ''
    }
    return
  }
  if (isHiddenDirSegment(entry.name)) return
  const dirPath = parentPath + entry.name
  result.dirs.add(dirPath)
  const reader = entry.createReader()
  // readEntries 每批最多返回 100 条，循环读到空批为止
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
    if (!batch.length) break
    for (const child of batch) await collectDroppedEntry(child, `${dirPath}/`, result)
  }
}
async function uploadDroppedEntries(entries) {
  const result = { files: [], dirs: new Set(), nonEmpty: new Set(), skipped: 0 }
  for (const entry of entries) await collectDroppedEntry(entry, '', result)
  if (result.skipped > 0) notify(`已跳过 ${result.skipped} 个隐藏或临时文件`)
  // 空目录先补齐（非空目录随文件上传自动创建），把整个文件夹结构搬进知识库
  const emptyDirs = [...result.dirs].filter(dir => !result.nonEmpty.has(dir))
  if (emptyDirs.length) {
    try { await ensureFolders(selectedBaseId.value, { folderId: selectedFolderId.value, paths: emptyDirs }) }
    catch (error) { handleError(error) }
  }
  if (result.files.length) await uploadFiles(result.files)
  else if (emptyDirs.length) await loadFolders()
}
async function handleDrop(event) {
  dragging.value = false
  if (!selectedBase.value || !can('upload')) return
  // entry 必须在事件同步阶段取出（DataTransfer 会在 await 后失效）
  const entries = Array.from(event.dataTransfer?.items || [])
    .map(item => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
    .filter(Boolean)
  if (entries.length) { await uploadDroppedEntries(entries); return }
  await uploadFiles(Array.from(event.dataTransfer?.files || []).map(file => ({ file, relativePath: '' })))
}
// crypto.randomUUID 仅安全上下文（HTTPS/localhost）可用；
// 局域网 HTTP 访问时回退 Math.random 生成（仅作前端队列 ID，无安全要求）
function genQueueId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}
// 重复文档预检：直接上传（不带相对路径）的文件与目标目录已有文档按文件名比对，
// 存在同名时弹确认框——确认则全部上传，取消则跳过重复文件仅传新文件。
// 文件夹导入的内容落入子目录，不在此检测；预检接口失败不阻塞上传。
async function confirmDuplicateUploads(items, baseId, folderId) {
  const directFiles = items.filter(it => it.file && !it.relativePath)
  if (!directFiles.length) return items
  const existingNames = new Set()
  try {
    let page = 1
    for (;;) {
      const response = await listDocuments(baseId, { folderId, directOnly: true, page, pageSize: 100 })
      const list = response.items || []
      for (const doc of list) existingNames.add(doc.name)
      const total = Number((response.page || {}).total || 0)
      if (!list.length || list.length < 100 || existingNames.size >= total) break
      page += 1
    }
  } catch (_) {
    return items
  }
  const duplicates = directFiles.filter(it => existingNames.has(it.file.name))
  if (!duplicates.length) return items
  const shown = duplicates.slice(0, 5).map(it => `「${it.file.name}」`).join('、')
  const suffix = duplicates.length > 5 ? ` 等 ${duplicates.length} 个文件` : ''
  const confirmed = await askConfirmation({
    title: '存在同名文档',
    description: `当前目录已有 ${duplicates.length} 个同名文件：${shown}${suffix}。继续上传会生成重复文档。`,
    confirmLabel: '仍然上传',
  })
  if (confirmed) return items
  const remaining = items.filter(it => it.relativePath || !existingNames.has(it.file.name))
  if (!remaining.length) {
    notify('所选文件均已存在，已取消上传')
    return []
  }
  notify(`已跳过 ${duplicates.length} 个重复文件，继续上传其余 ${remaining.length} 个`)
  return remaining
}
async function uploadFiles(items) {
  const uploadBaseId = selectedBaseId.value
  const uploadFolderId = selectedFolderId.value
  if (!uploadBaseId) return
  const uploadItems = await confirmDuplicateUploads(items, uploadBaseId, uploadFolderId)
  if (!uploadItems.length) return
  // 新批次开始时清掉上一批的"解析完成"行，面板聚焦本次导入
  uploadQueue.value = uploadQueue.value.filter(item => item.state !== 'ready')
  const pendingUploads = uploadItems.map(({ file, relativePath }) => {
    const queueId = genQueueId()
    const item = reactive({ id: queueId, optimisticId: `upload-${queueId}`, baseId: uploadBaseId, folderId: uploadFolderId, documentId: '', name: relativePath || file.name, progress: 0, state: 'uploading', error: '' })
    uploadQueue.value.push(item)
    if (selectedBaseId.value === uploadBaseId && selectedFolderId.value === uploadFolderId && !search.value.trim() && !statusFilter.value) {
      documents.value = [createOptimisticUploadDocument(file, item), ...documents.value]
      documentsPage.value = { ...documentsPage.value, total: Number(documentsPage.value.total || 0) + 1 }
    }
    return { file, relativePath, item }
  })
  for (const { file, relativePath, item } of pendingUploads) {
    try {
      const result = await uploadDocument(uploadBaseId, file, { folderId: uploadFolderId, relativePath, onProgress: value => {
        if (value >= 100) { item.progress = 95; item.state = 'saving' }
        else item.progress = Math.min(value, 95)
      } })
      item.documentId = result.document.id
      item.progress = 100; item.state = 'processing'
      const optimisticIndex = documents.value.findIndex(doc => doc.id === item.optimisticId)
      if (optimisticIndex >= 0 && (result.document.folder_id || '') === (uploadFolderId || '')) documents.value.splice(optimisticIndex, 1, { ...result.document, optimistic: false })
      else if (optimisticIndex >= 0) {
        // 文档按相对路径落入了子文件夹，当前视图不再展示
        documents.value.splice(optimisticIndex, 1)
        documentsPage.value = { ...documentsPage.value, total: Math.max(0, Number(documentsPage.value.total || 1) - 1) }
      }
    } catch (error) {
      const optimisticIndex = documents.value.findIndex(doc => doc.id === item.optimisticId)
      if (optimisticIndex >= 0) {
        documents.value.splice(optimisticIndex, 1)
        documentsPage.value = { ...documentsPage.value, total: Math.max(0, Number(documentsPage.value.total || 1) - 1) }
      }
      item.state = 'failed'; item.error = error.message; handleError(error)
    }
  }
  if (selectedBaseId.value === uploadBaseId) await Promise.all([loadDocuments(), loadFolders(), loadStatusOptions()])
  await Promise.all([loadOperations(), refreshBaseCounts()])
}
async function moveDoc(doc, folderId) { try { await moveDocument(doc.id, folderId); await Promise.all([loadDocuments(), loadFolders()]); notify('文档已移动') } catch (error) { handleError(error) } }
function openMoveDocument(doc) { movingDocument.value = doc; moveTargetFolderId.value = doc.folder_id || ''; showMoveModal.value = true }
async function confirmMoveDocument() {
  if (!movingDocument.value) return
  saving.value = true
  try { await moveDoc(movingDocument.value, moveTargetFolderId.value); showMoveModal.value = false }
  finally { saving.value = false }
}
async function retryDoc(doc) { try { await retryDocument(doc.id); await Promise.all([loadDocuments(), loadOperations()]); notify('已重新提交解析') } catch (error) { handleError(error) } }
async function pauseDoc(doc) { try { await pauseDocument(doc.id); await Promise.all([loadDocuments(), loadOperations()]); notify('已暂停解析任务，可随时恢复') } catch (error) { handleError(error) } }
async function resumeDoc(doc) { try { await resumeDocument(doc.id); await Promise.all([loadDocuments(), loadOperations()]); notify('已恢复解析任务，重新排队中') } catch (error) { handleError(error) } }
async function removeDocument(doc) {
  if (!await askConfirmation({ title: '删除这篇资料？', description: `「${doc.name}」将从知识库中永久移除。`, confirmLabel: '删除资料' })) return
  try { await deleteDocument(doc.id); await Promise.all([loadDocuments(), loadOperations(), refreshBaseCounts(), loadStatusOptions()]); notify('文档已删除') } catch (error) { handleError(error) } }

function previewable(doc) { return !doc.optimistic && (/^(application\/pdf|text\/|image\/)/.test(doc.content_type || '') || /\.(md|txt|docx?|xlsx?|pptx)$/i.test(doc.name || '')) }
async function previewDocument(doc) {
  // .doc 旧版格式已下线在线预览（原 doc->docx 转换链路移除），点击提示不支持
  if (/\.doc$/i.test(doc.name || '')) { notify('暂不支持 .doc 格式预览，请下载后查看', 'error'); return }
  closePreview()
  Object.assign(preview, { open: true, loading: true, name: doc.name, kind: '', url: '', text: '' })
  try {
    const result = await getDocumentBlob(doc.id, 'inline')
    if (result instanceof Error) throw result
    const { blob } = result
    if ((doc.content_type || '').startsWith('text/') || /\.(md|txt)$/i.test(doc.name)) { preview.kind = 'text'; preview.text = await blob.text() }
    else {
      preview.url = URL.createObjectURL(blob)
      if (/\.xlsx?$/i.test(doc.name)) preview.kind = 'excel'
      else
      preview.kind = /\.docx$/i.test(doc.name) ? 'docx' : /\.pptx$/i.test(doc.name) ? 'pptx' : (doc.content_type || '').startsWith('image/') ? 'image' : 'frame'
    }
  } catch (error) { closePreview(); handleError(error) }
  finally { preview.loading = false }
}
function closePreview() { if (preview.url) URL.revokeObjectURL(preview.url); Object.assign(preview, { open: false, loading: false, name: '', kind: '', url: '', text: '', buffer: null }) }
async function downloadDocument(doc) {
  try {
    notify('正在准备下载…')
    const { blob } = await getDocumentBlob(doc.id, 'attachment')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = doc.name
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => URL.revokeObjectURL(url), 30000)
    notify('已开始下载')
  } catch (error) { handleError(error) }
}

// ---------------- 权限管理（部门 > 处室 > 团队 三级勾选树） ----------------

// 将人员目录按 部门 > 处室 > 团队 组装为三级树；缺级人员挂在上一级直下。
// 节点持有 allUsers（子树全部人员），勾选态存在人员对象上，保证响应式。
function buildPermissionTree(users) {
  const sortByName = (a, b) => a.name.localeCompare(b.name, 'zh-CN')
  const deptMap = new Map()
  for (const user of users) {
    const deptId = String(user.department_id || '').trim()
    const deptKey = `dept:${deptId || '__none__'}`
    if (!deptMap.has(deptKey)) {
      deptMap.set(deptKey, {
        key: deptKey,
        name: String(user.department_name || '').trim() || deptId || '未分配部门',
        users: [],
        divisions: [],
        divisionMap: new Map(),
        allUsers: [],
      })
    }
    const dept = deptMap.get(deptKey)
    dept.allUsers.push(user)
    const divisionName = String(user.division_name || '').trim()
    if (!divisionName) {
      dept.users.push(user)
      continue
    }
    const divisionKey = `${deptKey}|division:${divisionName}`
    if (!dept.divisionMap.has(divisionKey)) {
      dept.divisionMap.set(divisionKey, { key: divisionKey, name: divisionName, users: [], teams: [], teamMap: new Map(), allUsers: [] })
      dept.divisions.push(dept.divisionMap.get(divisionKey))
    }
    const division = dept.divisionMap.get(divisionKey)
    division.allUsers.push(user)
    const teamName = String(user.team_name || '').trim()
    if (!teamName) {
      division.users.push(user)
      continue
    }
    const teamKey = `${divisionKey}|team:${teamName}`
    if (!division.teamMap.has(teamKey)) {
      division.teamMap.set(teamKey, { key: teamKey, name: teamName, users: [], allUsers: [] })
      division.teams.push(division.teamMap.get(teamKey))
    }
    const team = division.teamMap.get(teamKey)
    team.users.push(user)
    team.allUsers.push(user)
  }
  const tree = [...deptMap.values()].sort(sortByName)
  for (const dept of tree) {
    dept.divisions.sort(sortByName)
    for (const division of dept.divisions) {
      division.teams.sort(sortByName)
      delete division.teamMap
    }
    delete dept.divisionMap
  }
  return tree
}

function permSelectedCount(node) {
  return node.allUsers.reduce((count, user) => count + (user.checked ? 1 : 0), 0)
}
function permNodeChecked(node) {
  return node.allUsers.length > 0 && node.allUsers.every(user => user.checked)
}
function permNodeIndeterminate(node) {
  const selected = permSelectedCount(node)
  return selected > 0 && selected < node.allUsers.length
}
function togglePermNode(node, checked) {
  node.allUsers.forEach(user => { user.checked = checked })
}
function isPermCollapsed(key) { return Boolean(permissionCollapsed.value[key]) }
function togglePermExpand(key) {
  const next = { ...permissionCollapsed.value }
  if (next[key]) delete next[key]
  else next[key] = true
  permissionCollapsed.value = next
}

async function openPermissions() {
  const seq = ++permissionLoadSeq
  showPermissionModal.value = true
  permissionDirectoryLoading.value = true
  permissionCollapsed.value = {}
  try {
    const baseId = selectedBaseId.value
    const [permissionResponse, directoryResponse] = await Promise.all([
      listPermissions(baseId),
      listPermissionDirectory(baseId),
    ])
    if (seq !== permissionLoadSeq) return
    const checkedIds = new Set(
      (permissionResponse.items || [])
        .filter(item => item.subject_type === 'user')
        .map(item => item.subject_id),
    )
    permissionRestricted.value = Boolean(permissionResponse.restrict_access)
    const users = (directoryResponse.items || []).map(item => ({
      ...item,
      checked: checkedIds.has(item.user_id),
    }))
    permissionDirectory.value = users
    permissionTree.value = buildPermissionTree(users)
  } catch (error) {
    if (seq !== permissionLoadSeq) return
    handleError(error)
    permissionDirectory.value = []
    permissionTree.value = []
  } finally {
    if (seq === permissionLoadSeq) permissionDirectoryLoading.value = false
  }
}

async function savePermissions() {
  saving.value = true
  try {
    const userIds = permissionDirectory.value
      .filter(user => user.checked)
      .map(user => user.user_id)
    await replacePermissions(selectedBaseId.value, userIds)
    permissionRestricted.value = userIds.length > 0
    showPermissionModal.value = false
    notify('权限已保存')
  } catch (error) { handleError(error) } finally { saving.value = false }
}

// ---------------- 创建知识库：可见人员配置（复用权限管理三级树） ----------------

function isCreatePermCollapsed(key) { return Boolean(createPermCollapsed.value[key]) }
function toggleCreatePermExpand(key) {
  const next = { ...createPermCollapsed.value }
  if (next[key]) delete next[key]
  else next[key] = true
  createPermCollapsed.value = next
}

function resetCreatePermissions() {
  createPermLoadSeq += 1  // 在途请求作废
  createPermDirectory.value = []
  createPermTree.value = []
  createPermRestricted.value = false
  createPermCollapsed.value = {}
  createPermLoading.value = false
}

async function ensureCreatePermDirectory() {
  if (createPermDirectory.value.length || createPermLoading.value) return
  const seq = ++createPermLoadSeq
  createPermLoading.value = true
  try {
    const response = await listCreationPermissionDirectory()
    if (seq !== createPermLoadSeq) return
    const users = (response.items || []).map(item => ({ ...item, checked: false }))
    createPermDirectory.value = users
    createPermTree.value = buildPermissionTree(users)
  } catch (error) {
    if (seq !== createPermLoadSeq) return
    handleError(error)
    createPermDirectory.value = []
    createPermTree.value = []
  } finally {
    if (seq === createPermLoadSeq) createPermLoading.value = false
  }
}

// 创建弹窗切到公共空间时懒加载人员目录（个人空间无权限配置）
watch(() => [baseModalMode.value, baseForm.visibility], ([mode, visibility]) => {
  if (mode === 'create' && visibility === 'team') ensureCreatePermDirectory()
})

function touchAgentMessage() {
  if (!agentMessage.value) return
  agentMessage.value = {
    ...agentMessage.value,
    blocks: [...(agentMessage.value.blocks || [])],
  }
}

function findAgentBlock(type, event) {
  if (!agentMessage.value) return null
  const step = event.step || 0
  if (type === 'tool_call') {
    const callId = event.tool_call_id || `tool-${event.tool_name || ''}`
    return agentMessage.value.blocks.find(
      block => block.type === type && (block.id === callId || block.tool_call_id === callId)
    ) || null
  }
  return agentMessage.value.blocks.find(block => block.type === type && (block.step || 0) === step) || null
}

function ensureAgentBlock(type, event) {
  let block = findAgentBlock(type, event)
  if (block) return block
  blockOrder += 1
  block = { type, content: '', step: event.step || 0, order: blockOrder }
  if (type === 'tool_call') {
    const callId = event.tool_call_id || `tool-${event.tool_name || ''}`
    Object.assign(block, {
      id: callId,
      tool_call_id: callId,
      tool_name: event.tool_name || '',
      arguments: event.arguments || {},
      result: '',
      success: true,
    })
  }
  agentMessage.value.blocks.push(block)
  return block
}

function applyAgentEvent(event, baseId, generation) {
  if (
    selectedBaseId.value !== baseId ||
    generation !== askGeneration ||
    !agentMessage.value
  ) return

  if (event.type === 'knowledge_evidence') {
    agentMessage.value.knowledge_evidence = Array.isArray(event.evidence) ? event.evidence : []
    agentMessage.value.knowledge_warnings = Array.isArray(event.warnings) ? event.warnings : []
  } else if (event.type === 'thinking_start') {
    ensureAgentBlock('thinking', event)
  } else if (event.type === 'thinking') {
    const block = ensureAgentBlock('thinking', event)
    block.content = event.full_content || `${block.content || ''}${event.content || ''}`
    agentMessage.value.thinking = agentMessage.value.blocks
      .filter(item => item.type === 'thinking')
      .map(item => item.content || '')
      .join('')
  } else if (event.type === 'thinking_end') {
    const block = ensureAgentBlock('thinking', event)
    block.duration = event.duration || 0
  } else if (event.type === 'content_start') {
    ensureAgentBlock('content', event)
  } else if (event.type === 'content' && event.content) {
    const block = ensureAgentBlock('content', event)
    block.content = `${block.content || ''}${event.content}`
    agentMessage.value.content = `${agentMessage.value.content || ''}${event.content}`
  } else if (event.type === 'tool_call') {
    const block = ensureAgentBlock('tool_call', event)
    block.arguments = event.arguments || block.arguments || {}
  } else if (event.type === 'tool_result') {
    const block = ensureAgentBlock('tool_call', event)
    block.result = typeof event.result === 'string' ? event.result : JSON.stringify(event.result || '')
    block.success = event.success !== false
    block.duration = event.duration || 0
  } else if (event.type === 'done') {
    streamDone = true
    agentMessage.value.loading = false
    if (event.content) agentMessage.value.content = event.content
    for (const block of agentMessage.value.blocks) {
      if (block.type === 'thinking' && block.duration == null) block.duration = 0
    }
  } else if (event.type === 'error') {
    streamDone = true
    agentMessage.value.loading = false
    // 后端正常下发字符串；若拿到对象（如嵌套 error/message），提取可读文案，
    // 避免模板渲染出 "[object Object]"
    const content = event.content
    agentMessage.value.error = typeof content === 'string'
      ? (content || '处理失败')
      : (content?.message || content?.error || '处理失败')
    askError.value = agentMessage.value.error
  }
  touchAgentMessage()
}

async function stopQuestion(reason = 'user') {
  const controller = askController
  const sessionId = activeChatSessionId
  if (!controller && !asking.value) return
  askGeneration += 1
  askController = null
  activeChatSessionId = ''
  asking.value = false
  controller?.abort()
  if (agentMessage.value) {
    agentMessage.value = { ...agentMessage.value, loading: false }
  }
  if (sessionId) {
    try { await cancelMessage(sessionId) } catch (_) { /* 中止以本地状态为准 */ }
  }
  if (reason === 'user') {
    notify('已停止回答')
  }
}

function retryQuestion() {
  if (!lastQuestion.value || asking.value) return
  question.value = lastQuestion.value
  submitQuestion()
}

// 开启新对话：旧会话保留为历史（可在主界面会话列表回看），面板切换到全新会话
async function startNewChat() {
  const baseId = selectedBaseId.value
  if (!baseId || asking.value) return
  // 历史仍在拉取时界面暂时为空，但服务端可能已有记录——放行走 peek 判定；
  // 若在此返回，拉取完成后旧历史"复活"，表现为"点了新对话却仍有历史记录"
  if (!askHistory.value.length && !agentMessage.value && !askHistoryFetching) return
  const confirmed = await askConfirmation({
    title: '开启新对话？',
    description: '当前问答将保留为历史会话（可在主界面会话列表回看），之后提问将从全新对话开始。',
    confirmLabel: '开启新对话',
  })
  if (!confirmed) return
  // 先 peek 只读探测：有记录才轮换新会话；404 说明本就没有记录
  try {
    await prepareKnowledgeChatSession(baseId, { peek: true })
    try {
      await createKnowledgeChatRound(baseId)
      // 服务端已轮换出新会话，通知外层刷新会话列表（旧会话在列表中保留为历史）
      emit('chat-session-prepared')
    } catch (error) {
      handleError(error)
      return
    }
  } catch (error) {
    // peek 404 = 本就没有问答记录，仅清空本地即可；其他错误必须提示并中止，
    // 否则服务端仍是旧会话，下次提问会继续在旧会话里进行
    if (error?.response?.status !== 404) { handleError(error); return }
  }
  // 作废在途的历史拉取：其响应可能在轮换之后到达，会把旧历史写回界面
  askHistoryResetCount += 1
  askHistory.value = []
  askHistoryBaseId.value = ''
  agentMessage.value = null
  askError.value = ''
  lastQuestion.value = ''
  question.value = ''
  notify('已开启新对话')
}
// 面板问答区滚动到底部（发送新问题/载入历史后定位到最新一轮）
function scrollAskToBottom() {
  const el = askBody.value
  if (el && el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight
}

// 把服务端会话消息解析为面板问答条目（问题气泡 + 含知识引用的回答）
function parseAskHistory(messages) {
  const entries = []
  let pendingQuestion = ''
  for (const item of (messages || [])) {
    const content = String(item?.content || '').trim()
    if (!content) continue
    if (item.role === 'user') { pendingQuestion = content; continue }
    if (item.role === 'assistant') {
      entries.push({
        question: pendingQuestion,
        message: {
          ...item,
          id: item.id || `ke-history-${entries.length}`,
          role: 'assistant',
          blocks: Array.isArray(item.blocks) ? item.blocks : [],
          knowledge_evidence: Array.isArray(item.knowledge_evidence) ? item.knowledge_evidence : [],
          knowledge_warnings: Array.isArray(item.knowledge_warnings) ? item.knowledge_warnings : [],
          loading: false,
        },
      })
      pendingQuestion = ''
    }
  }
  return entries
}

// 拉取指定知识库的问答历史（打开面板/切换库时预取；首次提问时复用兜底）。
// preparedSessionId 由调用方传入时可跳过会话准备。预取走 peek 只读探测：
// 会话不存在返回 404 且不创建记录——点击知识库不应自动生成
// 「[知识库问答]」会话，只有真正提问才落记录。滚动必须等历史渲染完成
// （nextTick），否则 scrollTop 停留在赋值前的旧高度，表现为视口停在最早的对话。
async function loadAskHistory(baseId, preparedSessionId = '') {
  if (!baseId || askHistoryFetching || askHistoryBaseId.value === baseId) return
  askHistoryFetching = true
  const resetCount = askHistoryResetCount
  try {
    let sessionId = preparedSessionId
    if (!sessionId) {
      const prepared = await prepareKnowledgeChatSession(baseId, { peek: true })
      if (selectedBaseId.value !== baseId) return
      sessionId = prepared.session_id
    }
    activeChatSessionId = sessionId
    const history = await getChatHistory(sessionId)
    if (selectedBaseId.value !== baseId) return
    // 期间用户已「开启新对话」：丢弃本次响应，防止已删除的旧历史复活
    if (resetCount !== askHistoryResetCount) return
    askHistory.value = parseAskHistory(history.messages)
    // 仅会话确实存在时标记已拉取；peek 404 时不标记，下次打开重新探测
    askHistoryBaseId.value = baseId
    nextTick(() => scrollAskToBottom())
  } catch { /* 历史拉取失败不阻塞提问，面板欢迎页兜底 */ }
  finally { askHistoryFetching = false }
}

// 打开问答面板或切换知识库时预取历史，重新进入工作台也能直接回看此前的问答
watch(() => [askOpen.value, selectedBaseId.value], () => {
  if (askOpen.value && selectedBaseId.value) loadAskHistory(selectedBaseId.value)
})

async function submitQuestion(suggested = '') {
  if (suggested) question.value = suggested
  if (!question.value.trim() || !selectedBaseId.value || asking.value) return
  const baseId = selectedBaseId.value
  const generation = ++askGeneration
  askController = new AbortController()
  asking.value = true
  askError.value = ''
  streamDone = false
  blockOrder = 0
  // 上一轮已完成的问答归档到历史（失败的轮次不归档，随重试重新生成）
  if (agentMessage.value && !agentMessage.value.error) {
    askHistory.value.push({ question: lastQuestion.value, message: agentMessage.value })
  }
  agentMessage.value = {
    id: `knowledge-assistant-${Date.now()}`,
    role: 'assistant',
    content: '',
    thinking: '',
    tool_calls: [],
    blocks: [],
    knowledge_evidence: [],
    knowledge_warnings: [],
    loading: true,
    created_at: null,
  }
  lastQuestion.value = question.value.trim()
  // 发送即清空输入框（发送内容已存入 lastQuestion，不等回答完成）
  question.value = ''
  nextTick(() => scrollAskToBottom())
  try {
    const prepared = await prepareKnowledgeChatSession(baseId)
    if (generation !== askGeneration) return
    activeChatSessionId = prepared.session_id
    // 首次对该库提问时兜底拉取历史（面板打开/切换库时已预取过的不重复请求）
    if (!askHistory.value.length) await loadAskHistory(baseId, prepared.session_id)
    if (generation !== askGeneration) return
    // 问答会话刚入库（或复用）：通知外层立即刷新会话列表，避免"新对话/刷新页面才出现"。
    emit('chat-session-prepared')
    const onChunk = event => applyAgentEvent(event, baseId, generation)
    await sendMessage(
      prepared.session_id,
      lastQuestion.value,
      onChunk,
      askController.signal,
      true,
    )
    // 与新对话一样，SSE 连接意外结束时挂载到后台流并回放已有事件。
    if (!streamDone && generation === askGeneration && !askController.signal.aborted) {
      await attachStream(prepared.session_id, onChunk, askController.signal)
    }
  } catch (error) {
    if (error?.name !== 'AbortError' && generation === askGeneration) {
      askError.value = error?.message || '知识库问答失败'
      if (agentMessage.value) {
        agentMessage.value = { ...agentMessage.value, loading: false, error: askError.value }
      }
      handleError(error)
    }
  } finally {
    if (generation === askGeneration) {
      askController = null
      activeChatSessionId = ''
      asking.value = false
      if (agentMessage.value) agentMessage.value = { ...agentMessage.value, loading: false }
      // 问答结束后消息与 updated_at 已落库，再刷新一次让列表排序/时间同步。
      emit('chat-session-prepared')
    }
  }
}
async function loadOperations() { try { operations.value = (await listKnowledgeOperations({ pageSize: 100 })).items || [] } catch (error) { handleError(error) } }
function askConfirmation(options) { Object.assign(confirmation, { open: true, tone: 'danger', ...options }); return new Promise(resolve => { confirmResolver = resolve }) }
function resolveConfirmation(value) { confirmation.open = false; confirmResolver?.(value); confirmResolver = null }
function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    if (document.hidden || !selectedBaseId.value) return
    // The upload endpoint creates its database record before returning. Refreshing
    // the list in that window can display both the server row and our optimistic row.
    if (!hasCurrentUploadRequest.value) await loadDocuments()
    await loadOperations()
  }, 3000)
}
function stopPolling() { clearInterval(pollTimer); pollTimer = null }
function roleLabel(role) { return ({ viewer: '查看者', maintainer: '维护者', manager: '管理员' })[role] || role }
function spaceLabel(space) { return ({ personal: '个人空间', team: '公共空间' })[space] || space }
function folderPath(folder) {
  const parts = [folder.name]
  const visited = new Set([folder.id])
  let parent = folders.value.find(item => item.id === folder.parent_id)
  while (parent && !visited.has(parent.id)) { visited.add(parent.id); parts.unshift(parent.name); parent = folders.value.find(item => item.id === parent.parent_id) }
  return parts.join(' / ')
}
function documentStatusText(doc) {
  if (doc.status !== 'processing') return statusLabel(doc.status)
  const progress = Number(doc.progress || 0)
  if (progress < 0.02) return '正在读取原文'
  if (progress < 0.85) return `正在建立索引 ${Math.max(1, Math.round(progress * 100))}%`
  return '正在完成索引'
}
function processingElapsed(doc) {
  return processingElapsedText(doc, operations.value)
}
function statusLabel(status) { return ({ pending: '待处理', processing: '解析中', cancel_requested: '取消中', ready: '已就绪', failed: '失败', cancelled: '已取消', paused: '已暂停', deleting: '删除中', unknown: '未知' })[status] || status }
function operationStatusLabel(status) { return ({ accepted: '已受理', running: '进行中', succeeded: '成功', failed: '失败', partial_failed: '部分失败' })[status] || status }
function operationLabel(type) { return ({ dataset_create: '创建知识库', document_upload: '上传文档', document_parse: '解析文档' })[type] || type }

function formatSize(size = 0) { if (size < 1024) return `${size} B`; if (size < 1048576) return `${(size / 1024).toFixed(1)} KB`; return `${(size / 1048576).toFixed(1)} MB` }
function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }

// Vue 2.7 的生命周期适配对 async hook 返回值处理与 Vue 3 不同，用同步包装启动异步初始化。
onMounted(() => { initialize(); watchShellWidth() })
onUnmounted(() => { stopQuestion('unmount'); finishKnowledgeResize(); stopPolling(); closePreview(); clearTimeout(toastTimer); resolveConfirmation(false); stopShellWidthWatch() })

    return {
      addFolder,
      agentMessage,
      askHistory,
      askBody,
      askOpen,
      askError,
      askWidth,
      asking,
      baseCount,
      closeAskPanel,
      openAskPanel,
      baseForm,
      baseModalMode,
      baseQuery,
      bases,
      basesForSpace,
      can,
      canCreateInSpace,
      capabilities,
      clearFilters,
      closePreview,
      confirmation,
      confirmMoveDocument,
      createPermDirectoryTotal,
      createPermLoading,
      createPermRestricted,
      createPermSelectedTotal,
      createPermTree,
      isCreatePermCollapsed,
      toggleCreatePermExpand,
      currentFolder,
      currentFolders,
      documents,
      documentsPage,
      documentStatusText,
      downloadDocument,
      dragging,
      editBase,
      folderBreadcrumbs,
      folderForm,
      folderOptions,
      formatDate,
      formatSize,
      goParentFolder,
      handleDrop,
      handleFiles,
      handleFolderFiles,
      hasActiveWork,
      health,
      healthClass,
      healthLabel,
      isPermCollapsed,
      knowledgeLayoutStyle,
      lastQuestion,
      libraryWidth,
      loadDocuments,
      loadingDocuments,
      loadOperations,
      moveTargetFolderId,
      movingDocument,
      openCreateBase,
      openMoveDocument,
      openPermissions,
      operationLabel,
      operations,
      operationStatusLabel,
      permissionDirectoryLoading,
      permissionRestricted,
      permissionTree,
      permDirectoryTotal,
      permNodeChecked,
      permNodeIndeterminate,
      permSelectedCount,
      permSelectedTotal,
      preview,
      previewable,
      previewDocument,
      processingElapsed,
      question,
      questionSuggestions,
      removeBase,
      removeDocument,
      removeFolder,
      renameCurrentFolder,
      resizeKnowledgeWithKeyboard,
      resolveConfirmation,
      retryDoc,
      retryQuestion,
      roleLabel,
      saveBase,
      saveFolder,
      savePermissions,
      saving,
      search,
      selectedBase,
      selectedBaseId,
      selectedFolderId,
      selectedSpace,
      selectBase,
      selectFolder,
      selectSpace,
      showBaseModal,
      showFolderModal,
      showMoveModal,
      showPermissionModal,
      spaceLabel,
      spaces,
      startKnowledgeResize,
      startNewChat,
      statusFilter,
      statusOptions,
      pauseDoc,
      resumeDoc,
      statusLabel,
      stopQuestion,
      submitQuestion,
      teamSpaceManagement,
      teamSpaceEmptyHint,
      teamSpaceOptionSuffix,
      togglePermExpand,
      togglePermNode,
      toast,
      viewMode,
    }
  },
}
</script>

<style scoped>
.ke-shell{--ke-accent:var(--accent-color);--ke-accent-dark:var(--accent-color);--ke-accent-soft:color-mix(in srgb,var(--accent-color) 12%,transparent);--ke-ink:var(--text-primary);--ke-muted:var(--text-secondary);--ke-line:var(--border-color);--ke-bg:var(--bg-primary);position:relative;container:ke-shell/inline-size;flex:1;min-width:0;height:100%;display:flex;flex-direction:column;background:var(--ke-bg);color:var(--ke-ink);font-family:inherit}.ke-shell button,.ke-shell input,.ke-shell select,.ke-shell textarea{font:inherit}.ke-shell svg{width:16px;height:16px;flex:none}.ke-topbar{height:64px;flex:none;background:var(--bg-secondary);border-bottom:1px solid var(--ke-line);display:flex;align-items:center;justify-content:space-between;padding:0 20px;z-index:5}.ke-topbar-title,.ke-topbar-actions,.ke-inline-actions,.ke-title-row,.ke-base-meta,.ke-tool-row{display:flex;align-items:center}.ke-topbar-title{gap:10px}.ke-topbar-title h1{font-size:17px;line-height:1.2;margin:0;font-weight:650}.ke-topbar-title span{display:block;margin-top:3px;color:var(--text-secondary);font-size:11px}.ke-topbar-actions{gap:8px}.ke-button{height:34px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:9px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:6px;color:var(--text-secondary);font-size:12px;cursor:pointer;transition:.18s ease;box-sizing:border-box}.ke-button:hover{background:var(--bg-tertiary);border-color:var(--border-color)}.ke-button.primary{background:var(--ke-accent);border-color:var(--ke-accent);color:#fff;box-shadow:0 3px 10px color-mix(in srgb,var(--ke-accent) 22%,transparent)}.ke-button.primary:hover{background:var(--ke-accent-dark);border-color:var(--ke-accent-dark);transform:translateY(-1px)}.ke-button.quiet{background:var(--bg-secondary)}.ke-button.danger{background:#d94b4b;border-color:#d94b4b;color:#fff}.ke-button.icon-only{width:34px;padding:0}.ke-button:disabled,.ke-icon-button:disabled{opacity:.45;cursor:not-allowed;transform:none}.ke-icon-button{width:32px;height:32px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:9px;display:grid;place-items:center;color:var(--text-secondary);cursor:pointer;transition:.18s}.ke-icon-button:hover{background:var(--bg-tertiary);color:var(--text-primary)}.ke-icon-button.subtle{border-color:transparent;background:transparent}.danger-hover:hover,.danger-text{color:#cc4545!important;background:#fff3f2!important;border-color:#f3cdca!important}.ke-health{display:inline-flex;align-items:center;gap:6px;color:var(--text-secondary);font-size:11px;margin-right:4px}.ke-health i,.ke-status i{width:7px;height:7px;border-radius:50%;background:#e2a22e}.ke-health.ready i,.ke-status.ready i,.ke-status.succeeded i{background:var(--ke-accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--ke-accent) 12%,transparent)}
.ke-workspace{position:relative;flex:1;min-height:0;display:grid;grid-template-columns:var(--ke-library-width,252px) 6px minmax(490px,1fr) 6px var(--ke-ask-width,344px);gap:0}.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,252px) 6px minmax(540px,1fr)}.ke-library-panel,.ke-content-panel,.ke-ask-panel{min-height:0;background:#fff}.ke-library-panel{border-right:1px solid var(--ke-line);display:flex;flex-direction:column;padding:18px 12px 12px}.ke-resizer{position:relative;width:6px;min-width:6px;cursor:col-resize;touch-action:none;z-index:25;outline:none;background:var(--bg-secondary)}.ke-resizer:after{content:"";position:absolute;inset:0 2px;background:transparent;transition:.15s}.ke-resizer:hover:after,.ke-resizer:focus-visible:after,:global(body.ke-layout-resizing) .ke-resizer:after{background:var(--accent-color);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent-color) 14%,transparent)}.ke-library-heading{display:flex;align-items:center;justify-content:space-between;padding:0 4px 14px}.ke-library-heading>div{display:flex;flex-direction:column;gap:3px}.ke-library-heading strong{font-size:14px}.ke-library-heading small{font-size:10px;color:#9aa09c}.ke-search-box{height:34px;border:1px solid #e1e5e2;background:#fafbfa;border-radius:9px;display:flex;align-items:center;gap:7px;padding:0 9px;color:#9ba29e;transition:.18s}.ke-search-box:focus-within{border-color:#9fd8c0;background:#fff;box-shadow:0 0 0 3px rgba(37,161,111,.08)}.ke-search-box input{min-width:0;flex:1;border:0;outline:0;background:transparent;color:#303632;font-size:12px}.ke-search-box button{border:0;background:transparent;color:#9aa19c;display:grid;place-items:center;padding:0;cursor:pointer}.sidebar-search{margin-bottom:14px}.ke-space-tabs{display:flex;flex-direction:column;gap:3px}.ke-space-tabs button{height:36px;border:0;background:transparent;border-radius:9px;padding:0 10px;display:grid;grid-template-columns:18px 1fr auto;gap:8px;text-align:left;align-items:center;color:#68716b;font-size:12px;cursor:pointer}.ke-space-tabs button:hover{background:#f5f7f5}.ke-space-tabs button.active{background:var(--ke-accent-soft);color:var(--ke-accent-dark);font-weight:600}.ke-space-tabs b{font-weight:500;font-size:10px;color:#9aa19d}.ke-base-list{flex:1;min-height:0;overflow:auto;padding-top:14px;margin-top:12px;border-top:1px solid #f0f2f0}.ke-base-card{width:100%;border:1px solid transparent;background:transparent;border-radius:11px;padding:8px;display:flex;gap:9px;align-items:center;text-align:left;color:inherit;cursor:pointer;transition:.18s}.ke-base-card:hover{background:#f8faf8}.ke-base-card.active{background:#f2f8f5;border-color:#d9eee4}.ke-base-cover,.ke-hero-cover{display:grid;place-items:center;background:linear-gradient(145deg,#45bd8a,#229568);color:#fff;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}.ke-base-cover{width:34px;height:40px;border-radius:5px 9px 9px 5px}.ke-base-cover.team,.ke-hero-cover.team{background:linear-gradient(145deg,#5d9ee8,#557bc8)}.ke-base-cover.shared,.ke-hero-cover.shared{background:linear-gradient(145deg,#a788e7,#795fc4)}.ke-base-copy{display:flex;flex:1;min-width:0;flex-direction:column;gap:4px}.ke-base-copy strong{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-base-copy small{font-size:10px;color:#959c97}.ke-chevron{width:13px!important;color:#bec3bf;opacity:0}.ke-base-card:hover .ke-chevron,.ke-base-card.active .ke-chevron{opacity:1}.ke-empty-small{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#a2a9a4;text-align:center;font-size:11px;padding:32px 12px}.ke-empty-small svg{width:25px;height:25px}.ke-empty-small button{border:0;background:transparent;color:var(--ke-accent-dark);font-size:11px;cursor:pointer}.ke-library-tip{display:flex;align-items:flex-start;gap:7px;background:#f8faf8;border-radius:9px;padding:10px;color:#929994;font-size:9px;line-height:1.45}.ke-library-tip svg{width:14px;height:14px;color:#72a78e}
.ke-content-panel{position:relative;display:flex;flex-direction:column;overflow:hidden;background:#fff;container:ke-content/inline-size}.ke-base-hero{flex:none;padding:24px 26px 20px;display:flex;justify-content:space-between;gap:18px;border-bottom:1px solid var(--ke-line)}.ke-base-identity{display:flex;gap:15px;min-width:0}.ke-hero-cover{width:48px;height:58px;border-radius:7px 12px 12px 7px}.ke-hero-cover svg{width:22px;height:22px}.ke-title-row{gap:9px}.ke-title-row h2{margin:0;font-size:20px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-role{padding:3px 7px;background:#f1f4f2;border-radius:5px;color:#768078;font-size:9px}.ke-base-identity p{max-width:620px;margin:6px 0 10px;color:#868e89;font-size:11px;line-height:1.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-base-meta{gap:14px;color:#9aa09c;font-size:9px}.ke-base-meta span{display:flex;align-items:center;gap:4px}.ke-base-meta svg{width:12px;height:12px}.ke-inline-actions{gap:6px;align-self:flex-start}.ke-content-toolbar{flex:none;padding:13px 20px 12px;border-bottom:1px solid var(--ke-line)}.ke-folder-tabs{display:flex;align-items:center;gap:4px;overflow-x:auto;padding-bottom:11px}.ke-folder-tabs button{height:28px;border:0;background:transparent;border-radius:7px;padding:0 8px;display:flex;align-items:center;gap:5px;color:#777f7a;font-size:10px;white-space:nowrap;cursor:pointer}.ke-folder-tabs button:hover{background:#f5f7f5}.ke-folder-tabs button.active{background:#edf7f2;color:var(--ke-accent-dark);font-weight:600}.ke-folder-tabs button span{color:#9ca39e;font-size:9px}.ke-folder-tabs svg{width:12px;height:12px}.ke-folder-tabs .folder-add{color:var(--ke-accent-dark);margin-left:3px}.ke-folder-tabs .folder-manage{width:27px;padding:0;justify-content:center}.ke-tool-row{gap:7px}.ke-tool-row>.ke-search-box{flex:1;max-width:300px}.ke-select-wrap{height:34px;border:1px solid #e1e5e2;background:#fff;border-radius:9px;padding:0 8px;display:flex;align-items:center;color:#929a94}.ke-select-wrap select,.ke-folder-select select{border:0;outline:0;background:transparent;color:#636b66;font-size:10px}.ke-view-switch{height:34px;background:#f3f5f3;border-radius:9px;padding:3px;display:flex}.ke-view-switch button{width:28px;border:0;background:transparent;color:#9aa19c;border-radius:7px;display:grid;place-items:center;cursor:pointer}.ke-view-switch button.active{background:#fff;color:#3d4640;box-shadow:0 1px 4px rgba(30,45,35,.1)}
.ke-import-wrap{position:relative;display:inline-flex}
.ke-import-caret{width:13px!important;height:13px;transition:transform .15s ease}
.ke-import-wrap:hover .ke-import-caret,.ke-import-wrap:focus-within .ke-import-caret{transform:rotate(180deg)}
.ke-import-menu{position:absolute;top:calc(100% + 6px);right:0;z-index:60;min-width:180px;display:none;flex-direction:column;padding:5px;background:#fff;border:1px solid #e3e8e4;border-radius:11px;box-shadow:0 14px 36px rgba(23,35,27,.16)}
.ke-import-wrap:hover .ke-import-menu,.ke-import-wrap:focus-within .ke-import-menu{display:flex}
.ke-import-option{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;color:#3d4640;font-size:11px;cursor:pointer;white-space:nowrap}
.ke-import-option:hover{background:#f2f8f5;color:var(--ke-accent-dark)}
.ke-import-option svg{width:14px;height:14px;color:#69726c}
.ke-documents{flex:1;min-height:0;overflow:auto;padding:18px 20px 28px}.ke-documents.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));align-content:start;gap:10px}.ke-document-card{position:relative;border:1px solid #e6eae7;border-radius:12px;background:#fff;padding:10px 12px;min-width:0;transition:.18s}.grid .ke-document-card{display:flex;flex-direction:column}.ke-document-card:hover{border-color:#ccd8d1;box-shadow:0 8px 24px rgba(39,64,48,.07);transform:translateY(-1px)}.ke-doc-open{width:100%;border:0;background:transparent;padding:0;display:grid;grid-template-columns:30px minmax(0,1fr);align-items:start;column-gap:8px;text-align:left;min-width:0;color:inherit;cursor:pointer}.ke-doc-open:disabled{cursor:default}.ke-file-icon{width:30px;min-width:30px;height:34px;box-sizing:border-box;display:grid;place-items:center;border-radius:8px;background:#f7f9f7;overflow:hidden}.ke-document-copy{width:100%;min-width:0;overflow:hidden;display:flex;flex-direction:column;gap:3px}.ke-doc-title{display:flex;align-items:baseline;gap:6px;min-width:0;max-width:100%}.ke-document-copy strong{min-width:0;font-size:11px;line-height:1.45;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-document-copy small{color:#9aa19c;font-size:9px;white-space:nowrap;flex-shrink:0}.ke-document-details{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:8px}.grid .ke-document-details{margin-top:auto;padding-top:8px}.ke-status{display:inline-flex;align-items:center;gap:5px;color:#6e7771;font-size:9px;white-space:nowrap}.ke-status i{width:6px;height:6px}.ke-status em{font-style:normal;color:#9aa19c}.ke-status.processing i,.ke-status.running i{background:#4b91e4;animation:ke-pulse 1.2s infinite}.ke-status.failed i,.ke-status.partial_failed i{background:#e25555}.ke-folder-select{min-width:0;display:flex;align-items:center;gap:3px;color:#9aa19c}.ke-folder-select svg{width:11px;height:11px}.ke-folder-select select{max-width:80px;text-overflow:ellipsis}.ke-error-text{font-size:9px;color:#cc4b4b;line-height:1.4;margin:7px 0 0}.ke-row-actions{position:absolute;right:9px;top:9px;display:flex;align-items:center;gap:2px;background:rgba(255,255,255,.96);border-radius:8px;padding:2px;opacity:0;transform:translateY(-3px);transition:.16s;box-shadow:0 2px 10px rgba(30,45,35,.08)}.ke-document-card:hover .ke-row-actions,.ke-row-actions:focus-within{opacity:1;transform:none}.ke-row-actions button{height:22px;border:0;background:transparent;border-radius:6px;padding:0 5px;display:flex;align-items:center;gap:3px;color:#69726c;font-size:9px;cursor:pointer}.ke-row-actions button:hover{background:#f2f5f3;color:#26312a}.ke-row-actions svg{width:11px;height:11px}.list.ke-documents{padding-top:8px}.list .ke-document-card{border-width:0 0 1px;border-radius:0;padding:7px 8px;display:grid;grid-template-columns:minmax(220px,1fr) 240px 145px;align-items:center;gap:10px}.list .ke-doc-open{align-items:center}
.list .ke-document-card:hover{transform:none;box-shadow:none;background:#fafbfa}.list .ke-document-details{margin:0 -10px 0 0;align-self:center;justify-content:flex-end}.list .ke-row-actions{position:static;opacity:1;transform:none;box-shadow:none;justify-content:flex-end;align-self:center}.list .ke-row-actions button span{display:none}.list .ke-error-text{grid-column:1/-1}.ke-document-loading{grid-column:1/-1;min-height:220px;display:flex;align-items:center;justify-content:center;color:var(--text-secondary);font-size:12px}.ke-document-loading p{margin:0;display:inline-flex;align-items:center}.ke-dot{width:4px;height:4px;margin-left:4px;border-radius:50%;background:currentColor;animation:ke-dot-pulse 1.2s infinite}.ke-dot:nth-child(2){animation-delay:.2s}.ke-dot:nth-child(3){animation-delay:.4s}.ke-empty-documents{grid-column:1/-1;align-self:center;margin:auto;display:flex;flex-direction:column;align-items:center;text-align:center;padding:45px 20px}.ke-empty-icon,.ke-empty-illustration{width:60px;height:60px;border-radius:18px;background:var(--ke-accent-soft);color:var(--ke-accent);display:grid;place-items:center}.ke-empty-icon svg,.ke-empty-illustration svg{width:27px;height:27px}.ke-empty-documents .ke-import-wrap{width:auto;height:auto}.ke-empty-documents h3{margin:14px 0 5px;font-size:14px}.ke-empty-documents p{margin:0 0 15px;color:#929994;font-size:11px}.ke-drop-mask{position:absolute;inset:10px;z-index:15;border:2px dashed #71c39f;border-radius:16px;background:rgba(237,250,244,.94);display:grid;place-items:center;pointer-events:none}.ke-drop-mask>div{display:flex;flex-direction:column;align-items:center;gap:7px;color:var(--ke-accent-dark)}.ke-drop-mask svg{width:36px;height:36px}.ke-drop-mask strong{font-size:15px}.ke-drop-mask span{color:#7b8c82;font-size:11px}
.ke-ask-panel{border-left:1px solid var(--ke-line);display:flex;flex-direction:column;overflow:hidden;background:#fcfdfc}.ke-ask-header{height:67px;flex:none;padding:0 14px;display:flex;align-items:center;gap:9px;border-bottom:1px solid var(--ke-line);background:#fff}.ke-ai-avatar{width:31px;height:31px;border-radius:10px;background:linear-gradient(145deg,#32b981,#21865f);color:#fff;display:grid;place-items:center;box-shadow:0 4px 12px rgba(37,161,111,.2)}.ke-ai-avatar.small{width:23px;height:23px;border-radius:7px}.ke-ai-avatar.small svg{width:12px;height:12px}.ke-ask-header>div:nth-child(2){display:flex;flex:1;min-width:0;flex-direction:column;gap:3px}.ke-ask-header strong{font-size:12px}.ke-ask-header small{font-size:9px;color:#969d98;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-answer{flex:1;min-height:0;overflow:auto;padding:18px}.ke-history-entry{display:flex;flex-direction:column;gap:8px;padding-bottom:12px;margin-bottom:12px;border-bottom:1px dashed #e8ece9}.ke-history-entry:last-of-type{border-bottom:0}.ke-answer-welcome{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.ke-answer-orb{width:52px;height:52px;border-radius:18px;background:radial-gradient(circle at 30% 25%,#fff 0,#e4f7ee 35%,#cceede 100%);color:var(--ke-accent);display:grid;place-items:center;box-shadow:0 8px 25px rgba(38,126,88,.12)}.ke-answer-orb svg{width:24px;height:24px}.ke-answer-welcome h3{font-size:14px;margin:15px 0 6px}.ke-answer-welcome>p{max-width:235px;margin:0;color:#8d9690;font-size:10px;line-height:1.7}.ke-question-suggestions{width:100%;margin-top:19px;display:flex;flex-direction:column;gap:7px}.ke-question-suggestions button{border:1px solid #e3e8e4;background:#fff;border-radius:10px;padding:10px 11px;display:flex;justify-content:space-between;align-items:center;color:#626b65;text-align:left;font-size:10px;cursor:pointer;transition:.16s}.ke-question-suggestions button:hover{border-color:#b9ddcb;background:#f5fbf8;color:var(--ke-accent-dark)}.ke-question-suggestions svg{width:12px;height:12px}.ke-answer-loading{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#8b948e;font-size:10px}.ke-question-bubble{margin-left:auto;width:fit-content;max-width:100%;padding:9px 11px;border-radius:11px 3px 11px 11px;background:#eef7f3;color:#3f4d45;font-size:11px;line-height:1.55}.ke-answer-label{display:flex;align-items:center;gap:7px;margin:18px 0 10px;font-size:10px}.ke-answer-text{font-size:11px;line-height:1.85;color:#3e4741;white-space:pre-wrap}.ke-evidence-title{display:flex;align-items:center;gap:5px;margin:18px 0 7px;color:#737d76;font-size:10px;font-weight:600}.ke-evidence-title svg{width:13px;height:13px}.ke-evidence{width:100%;margin-top:7px;padding:10px;border:1px solid #e3e8e4;border-radius:10px;background:#fff;text-align:left;color:inherit;cursor:pointer}.ke-evidence:hover{border-color:#c6ddd1;background:#fbfdfc}.ke-evidence>div{display:flex;gap:7px;align-items:center;font-size:9px}.ke-evidence b{width:18px;height:18px;border-radius:5px;background:var(--ke-accent-soft);color:var(--ke-accent-dark);display:grid;place-items:center}.ke-evidence strong{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-evidence span{margin-left:auto;color:#9aa19c}.ke-evidence p{font-size:9px;line-height:1.55;color:#747d77;margin:7px 0 0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.ke-warning{display:flex;gap:6px;color:#a56a14;font-size:9px;line-height:1.5}.ke-warning svg{width:13px;height:13px}.ke-ask-form{flex:none;padding:12px 14px 13px}.ke-composer{border:1px solid #dce3de;background:#fff;border-radius:13px;padding:8px 9px;box-shadow:0 5px 18px rgba(42,62,49,.06);transition:.18s}.ke-composer:focus-within{border-color:#91ceb2;box-shadow:0 0 0 3px rgba(37,161,111,.08)}.ke-composer.disabled{background:#f7f8f7}.ke-composer textarea{width:100%;min-height:50px;box-sizing:border-box;border:0;outline:0;resize:none;background:transparent;color:#303733;font-size:11px;line-height:1.55}.ke-composer>div{display:flex;align-items:center;justify-content:space-between}.ke-composer>div span{display:flex;align-items:center;gap:4px;color:#9aa19c;font-size:8px}.ke-composer>div span svg{width:11px;height:11px}.ke-composer>div button{width:28px;height:28px;border:0;border-radius:9px;background:var(--ke-accent);color:#fff;display:grid;place-items:center;cursor:pointer}.ke-composer>div button:disabled{background:#d8ddda;cursor:not-allowed}.ke-ask-form>small{display:block;margin-top:6px;text-align:center;color:#a1a7a3;font-size:8px}.ke-ask-fab{position:absolute;right:20px;bottom:20px;height:40px;border:0;border-radius:20px;background:var(--ke-accent);color:#fff;padding:0 15px;display:flex;align-items:center;gap:7px;box-shadow:0 8px 24px rgba(37,161,111,.25);cursor:pointer;z-index:10}.ke-ask-fab span{font-size:11px}
.ke-loading-page,.ke-empty-page{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#858e88}.ke-loading-page p{font-size:11px}.ke-empty-page h2{font-size:17px;color:#303632;margin:15px 0 6px}.ke-empty-page p{font-size:11px;margin:0 0 16px}.ke-empty-page.compact{height:100%}.ke-spinner{width:22px;height:22px;border:2px solid #dce9e2;border-top-color:var(--ke-accent);border-radius:50%;animation:ke-spin .75s linear infinite}
.ke-modal-mask,.ke-drawer-mask{position:fixed;inset:0;background:rgba(26,31,28,.38);z-index:1200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px)}.ke-modal{width:min(430px,calc(100vw - 32px));max-height:90vh;overflow:auto;background:#fff;border-radius:16px;padding:21px;box-shadow:0 24px 65px rgba(23,35,27,.2)}.ke-modal.small{width:min(360px,calc(100vw - 32px))}.ke-modal.wide{width:min(600px,calc(100vw - 32px))}.ke-modal header,.ke-preview-modal header,.ke-drawer header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.ke-modal header h3,.ke-preview-modal h3,.ke-drawer h2{margin:0;font-size:16px}.ke-modal header p,.ke-drawer header p{margin:4px 0 0;color:#929a95;font-size:10px;line-height:1.5}.ke-modal header>button,.ke-preview-modal header>button,.ke-drawer header>button{width:30px;height:30px;border:0;background:transparent;border-radius:8px;display:grid;place-items:center;color:#7d8580;cursor:pointer}.ke-modal header>button:hover,.ke-preview-modal header>button:hover,.ke-drawer header>button:hover{background:#f3f5f3}.ke-modal>label{display:flex;flex-direction:column;gap:6px;margin-top:16px;color:#59615c;font-size:11px}.ke-modal input,.ke-modal select,.ke-modal textarea{width:100%;box-sizing:border-box;border:1px solid #dfe4e0;border-radius:9px;background:#fff;padding:9px 10px;color:#303632;outline:0;font-size:11px}.ke-modal input:focus,.ke-modal select:focus,.ke-modal textarea:focus{border-color:#91ceb2;box-shadow:0 0 0 3px rgba(37,161,111,.07)}.ke-modal textarea{min-height:82px;resize:vertical}.ke-modal label>small{color:#9ba19d;font-size:9px;line-height:1.5}.ke-modal footer{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.ke-perm-loading{margin-top:18px;padding:26px 0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--text-secondary);font-size:11px}.ke-perm-loading p{margin:0}.ke-perm-loading .ke-spinner{flex:none}.ke-perm-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:16px;padding:8px 12px;border:1px solid #e3e7e4;border-radius:10px;background:#f8faf8;color:#7d8580;font-size:10px}.ke-perm-toolbar strong{color:var(--ke-accent-dark);font-size:12px}.ke-perm-tree{margin-top:7px;border:1px solid #e3e7e4;border-radius:10px;max-height:340px;overflow:auto;padding:6px 10px;background:#fff}.ke-perm-branch+.ke-perm-branch{margin-top:4px}.ke-perm-row{display:grid;grid-template-columns:24px 18px 1fr auto;gap:7px;align-items:center;padding:7px 4px;border-radius:8px}.ke-perm-row:hover{background:#f7faf8}.ke-perm-row.level-dept>.ke-perm-name{font-weight:650;font-size:12px;color:#3d4640}.ke-perm-row.level-division>.ke-perm-name{font-weight:600;font-size:11px;color:#4a544d;padding-left:2px}.ke-perm-row.level-team>.ke-perm-name{font-weight:500;font-size:11px;color:#59615c;padding-left:2px}.ke-perm-name{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-perm-count{color:#9aa19c;font-size:9px}.ke-perm-caret{width:22px;height:22px;padding:0;border:0;background:transparent;border-radius:6px;display:grid;place-items:center;color:#9aa19c;cursor:pointer}.ke-perm-caret:hover{background:#eef3ef;color:#59615c}.ke-perm-caret svg{width:13px;height:13px;transition:transform .15s ease}.ke-perm-caret svg.rotated{transform:rotate(-90deg)}.ke-perm-children{margin-left:24px;border-left:1px dashed #dfe4e0;padding-left:8px}.ke-perm-user{display:grid;grid-template-columns:18px 1fr auto;gap:8px;align-items:center;padding:5px 4px;border-radius:8px;cursor:pointer;color:#3d4640;font-size:11px}.ke-perm-user:hover{background:#f7faf8}.ke-perm-user span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-perm-user small{color:#9aa19c;font-size:9px}.ke-perm-empty{margin:0;padding:22px 10px;color:#9aa19c;text-align:center;font-size:10px}.ke-perm-tree input[type=checkbox]{width:15px;height:15px;flex:none;box-sizing:border-box;margin:0;padding:0;border:0;background:transparent;accent-color:var(--ke-accent);cursor:pointer}.ke-modal.confirm{text-align:center;width:min(360px,calc(100vw - 32px));overflow:visible}.ke-confirm-icon{width:45px;height:45px;margin:0 auto 12px;border-radius:14px;background:#fff1ef;color:#d34c4c;display:grid;place-items:center}.ke-confirm-icon svg{width:22px;height:22px}.ke-modal.confirm h3{font-size:16px;margin:0}.ke-modal.confirm>p{font-size:10px;color:#838b86;line-height:1.65;margin:8px 10px}.ke-modal.confirm footer{justify-content:center}.preview-mask{padding:24px}.ke-preview-modal{width:min(1080px,94vw);height:min(820px,90vh);background:#fff;border-radius:15px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 24px 70px rgba(18,29,22,.24)}.ke-preview-modal header{height:55px;flex:none;align-items:center;padding:0 16px;border-bottom:1px solid var(--ke-line)}.ke-preview-modal header>div{display:flex;align-items:center;gap:8px;min-width:0}.ke-preview-modal h3{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-preview-modal iframe,.ke-preview-modal img,.ke-preview-modal pre{flex:1;min-height:0;width:100%;border:0;object-fit:contain;background:#f5f7f5;overflow:auto;box-sizing:border-box;padding:16px}.ke-drawer-mask{justify-content:flex-end;align-items:stretch}.ke-drawer{width:min(400px,90vw);background:#fff;padding:20px;overflow:auto;box-shadow:-18px 0 50px rgba(28,38,31,.13)}.ke-drawer .refresh{margin:15px 0;width:100%}.ke-operation{padding:12px 0;border-top:1px solid #edf0ed}.ke-operation>div:first-child{display:grid;grid-template-columns:30px 1fr auto;gap:8px;align-items:center;margin-bottom:9px}.ke-operation-icon{width:28px;height:28px;background:#f2f6f3;color:#6f7c74;border-radius:8px;display:grid;place-items:center}.ke-operation>div:first-child>div{display:flex;flex-direction:column;gap:2px}.ke-operation strong{font-size:10px}.ke-operation small{color:#949c97;font-size:8px}.ke-operation p{color:#c94a4a;font-size:9px}.ke-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#26342c;color:#fff;border-radius:10px;padding:10px 14px;z-index:1600;display:flex;align-items:center;gap:7px;font-size:11px;box-shadow:0 9px 28px rgba(22,36,27,.24)}.ke-toast.error{background:#b83f43}.ke-toast svg{width:14px;height:14px}.ke-toast-enter-active,.ke-toast-leave-active{transition:.2s}.ke-toast-enter-from,.ke-toast-leave-to{opacity:0;transform:translate(-50%,8px)}
.ke-workspace{grid-template-columns:var(--ke-library-width,252px) 6px minmax(490px,1fr) 6px var(--ke-ask-width,344px)}.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,252px) 6px minmax(540px,1fr)}
.sidebar-search{margin-bottom:12px}.ke-space-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:4px}.ke-space-group{border-radius:11px;overflow:hidden}.ke-space-group.active{background:#f8faf8}.ke-space-trigger{width:100%;min-height:48px;border:0;background:transparent;border-radius:10px;padding:7px 8px;display:grid;grid-template-columns:19px minmax(0,1fr) auto 14px;gap:8px;align-items:center;text-align:left;color:#5f6862;cursor:pointer}.ke-space-trigger:hover{background:#f4f7f5}.ke-space-group.active>.ke-space-trigger{color:var(--ke-accent-dark);background:#eef7f3}.ke-space-trigger>span{display:flex;min-width:0;flex-direction:column;gap:2px}.ke-space-trigger strong{font-size:11px}.ke-space-trigger small{font-size:8px;color:#999f9b;font-weight:400}.ke-space-trigger b{font-size:9px;font-weight:500;color:#979f99}.ke-space-trigger>svg:last-child{width:12px;height:12px;transition:transform .18s}.ke-space-trigger>svg.rotated{transform:rotate(180deg)}.ke-space-bases{margin:5px 0 8px 17px;padding-left:9px;border-left:2px solid #d9e9e0;display:flex;flex-direction:column;gap:2px}.ke-space-bases .ke-base-card{border-radius:9px;padding:6px 7px;gap:8px}.ke-space-bases .ke-base-card.active{background:#fff;box-shadow:0 2px 8px rgba(31,68,47,.05)}.ke-space-bases .ke-base-cover{width:26px;height:30px;flex:none}.ke-space-bases .ke-base-copy{gap:1px}.ke-space-bases .ke-base-copy strong{font-size:11px;line-height:1.3}.ke-space-bases .ke-base-copy small{font-size:9px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-space-empty{padding:10px 6px;color:#9ba29d;font-size:9px;display:flex;align-items:center;justify-content:space-between;gap:6px}.ke-space-empty>small{display:block;max-width:105px;text-align:right;color:#9ba29d;line-height:1.35}.ke-space-empty button{border:0;background:transparent;color:var(--ke-accent-dark);display:flex;align-items:center;gap:3px;cursor:pointer}
.ke-folder-breadcrumbs{min-height:30px;display:flex;align-items:center;gap:3px;padding-bottom:11px;overflow-x:auto}.ke-folder-breadcrumbs>svg,.ke-folder-crumb>svg{width:12px;height:12px;color:#b3b8b5}.ke-folder-breadcrumbs>button,.ke-folder-crumb>button{height:28px;border:0;background:transparent;border-radius:7px;padding:0 7px;color:#8b928e;font-size:10px;white-space:nowrap;cursor:pointer}.ke-folder-breadcrumbs>button:hover,.ke-folder-breadcrumbs>button.current,.ke-folder-crumb>button:hover,.ke-folder-crumb>button.current{background:#f1f6f3;color:#27312b;font-weight:600}.ke-folder-crumb{display:inline-flex;align-items:center;gap:3px}.ke-folder-breadcrumbs .ke-folder-back{width:28px;padding:0;display:grid;place-items:center}.ke-folder-actions{display:flex;align-items:center;gap:3px;margin-left:auto;padding-left:12px}.ke-folder-actions button{height:28px;border:0;background:#f5f7f5;border-radius:7px;padding:0 7px;display:flex;align-items:center;gap:4px;color:#6e7771;font-size:9px;white-space:nowrap;cursor:pointer}.ke-folder-actions button:first-child{color:var(--ke-accent-dark);background:#edf7f2}.ke-folder-actions svg{width:12px;height:12px}
.ke-folder-grid{grid-column:1/-1;display:flex;flex-direction:column;gap:2px;margin-bottom:11px}.ke-folder-card{width:100%;height:62px;border:0;border-bottom:1px solid #f0f2f0;background:#fff;padding:5px 10px;display:grid;grid-template-columns:54px minmax(0,1fr) 18px;gap:10px;align-items:center;text-align:left;color:inherit;border-radius:8px;cursor:pointer}.ke-folder-card:hover{background:#f8faf8}.ke-folder-art{position:relative;width:49px;height:35px;border-radius:4px 5px 5px 5px;background:linear-gradient(180deg,#bce7d1,#a8dbc0);color:#fff;display:grid;place-items:center}.ke-folder-art:before{content:"";position:absolute;left:3px;top:-6px;width:22px;height:8px;border-radius:4px 5px 0 0;background:#9bd5b8}.ke-folder-art svg{position:relative;width:23px;height:23px}.ke-folder-card>span:nth-child(2){display:flex;min-width:0;flex-direction:column;gap:4px}.ke-folder-card strong{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-folder-card small{font-size:9px;color:#acb1ae}.ke-folder-card>svg{color:#c1c6c2;width:14px;height:14px}
.ke-status.paused i{background:#9aa19c}.ke-status.processing i,.ke-status.running i{animation:none}.ke-processing-age{margin-left:auto;color:#9aa19c;font-size:9px;white-space:nowrap}.ke-preview-modal>div:not(header){flex:1;min-height:0;overflow:auto}.ke-preview-modal ::v-deep .docx-wrapper{min-height:100%;background:#eef1ef}.ke-preview-modal ::v-deep .vue-office-excel{height:100%}.ke-preview-modal ::v-deep .pptx-preview-wrapper{height:100%;overflow:auto}
@keyframes ke-pulse{50%{opacity:.35}}@keyframes ke-spin{to{transform:rotate(360deg)}}@keyframes ke-dot-pulse{0%,100%{opacity:.2}50%{opacity:1}}
@container ke-shell (max-width:1100px){.ke-workspace,.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,220px) 6px minmax(0,1fr) 6px 0}.ke-workspace:not(.ask-closed) .ke-content-panel{margin-right:min(var(--ke-ask-width,360px),50cqw)}.ke-ask-panel{position:absolute;right:0;top:0;bottom:0;width:min(var(--ke-ask-width,360px),50cqw);z-index:20;box-shadow:-14px 0 35px rgba(28,43,33,.13)}.ke-base-meta span:last-child{display:none}.ke-tool-row{flex-wrap:wrap}.ke-tool-row>.ke-search-box{max-width:none}.list .ke-document-card{grid-template-columns:minmax(180px,1fr) 190px 130px}/* 浮层模式下文档列被问答面板压缩：文档名放开换行，保证完整可见不被截断 */.ke-document-copy strong{white-space:normal;word-break:break-word}}
/* 内容面板被拖窄时：隐藏大小/日期等次要信息，为文档名让出空间；文档名放开换行防截断。 */
@container ke-content (max-width:640px){.list .ke-doc-title small{display:none}.ke-document-copy strong{white-space:normal;word-break:break-word}}
@container ke-shell (max-width:700px){.ke-topbar{padding:0 10px}.ke-topbar-title span,.ke-topbar-actions .quiet{display:none}.ke-workspace,.ke-workspace.ask-closed{display:block}.ke-library-panel,.ke-resizer.library{display:none}.ke-content-panel{height:100%}.ke-base-hero{padding:18px 15px}.ke-inline-actions{position:absolute;right:14px}.ke-base-identity{padding-right:80px}.ke-base-meta{flex-wrap:wrap}.ke-content-toolbar{padding:11px 12px}.ke-documents{padding:12px}.ke-documents.grid{grid-template-columns:1fr}.ke-view-switch{display:none}.list .ke-document-card{display:block}.list .ke-document-details{margin-top:12px;margin-right:0}.list .ke-row-actions{margin-top:8px;justify-content:flex-start}.ke-base-identity p{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}/* 左侧导航隐藏后文档列占满容器：问答浮层拉宽上限收紧到给文档列保底 220px */.ke-ask-panel{width:min(var(--ke-ask-width,360px),calc(100cqw - 220px))}.ke-workspace:not(.ask-closed) .ke-content-panel{margin-right:min(var(--ke-ask-width,360px),calc(100cqw - 220px))}}

/* EasyAgent surface and dark-mode alignment. */
.ke-library-panel,.ke-content-panel,.ke-ask-panel,.ke-ask-header,.ke-document-card,.ke-folder-card,.ke-modal,.ke-preview-modal,.ke-drawer,.ke-composer,.ke-evidence,.ke-question-suggestions button{background:var(--bg-secondary);color:var(--text-primary)}
.ke-search-box,.ke-select-wrap,.ke-view-switch,.ke-role,.ke-operation-icon,.ke-file-icon{background:var(--bg-tertiary);border-color:var(--border-color);color:var(--text-secondary)}
.ke-search-box input,.ke-select-wrap select,.ke-folder-select select,.ke-modal input,.ke-modal select,.ke-modal textarea,.ke-composer textarea{color:var(--text-primary);background:transparent}
.ke-search-box:focus-within,.ke-modal input:focus,.ke-modal select:focus,.ke-modal textarea:focus,.ke-composer:focus-within{border-color:var(--accent-color);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent-color) 14%,transparent)}
.ke-space-group.active,.ke-space-trigger:hover,.ke-base-card:hover,.ke-folder-card:hover,.list .ke-document-card:hover,.ke-row-actions button:hover{background:var(--bg-tertiary)}
.ke-space-group.active>.ke-space-trigger,.ke-base-card.active,.ke-folder-actions button:first-child,.ke-question-bubble{background:color-mix(in srgb,var(--accent-color) 11%,transparent);color:var(--accent-color)}
.ke-space-bases .ke-base-card.active,.ke-view-switch button.active,.ke-row-actions{background:var(--bg-secondary)}
.ke-space-bases{border-left-color:color-mix(in srgb,var(--accent-color) 35%,var(--border-color))}
.ke-modal input,.ke-modal select,.ke-modal textarea,.ke-operation{border-color:var(--border-color)}
.ke-preview-modal iframe,.ke-preview-modal img,.ke-preview-modal pre{background:var(--bg-primary)}
.ke-ai-avatar{background:linear-gradient(145deg,#38bdf8,#0284c7);box-shadow:0 4px 12px color-mix(in srgb,var(--accent-color) 24%,transparent)}
.ke-folder-art{background:linear-gradient(180deg,#7dd3fc,#38bdf8)}.ke-folder-art:before{background:#0ea5e9}
.ke-preview-loading{flex:1;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--text-secondary);font-size:11px}.ke-preview-loading p{margin:0}.ke-preview-loading .ke-spinner{flex:none}
.ke-stop-button{color:#d14b4b!important}.ke-answer-text{min-height:18px}.ke-stream-cursor{display:inline-block;width:2px;height:1em;margin-left:3px;vertical-align:-2px;background:var(--accent-color);animation:ke-cursor-blink .8s steps(2,end) infinite}.ke-ask-error{margin-top:14px;padding:11px;border:1px solid color-mix(in srgb,#d14b4b 28%,var(--border-color));border-radius:10px;background:color-mix(in srgb,#d14b4b 7%,var(--bg-secondary));color:var(--text-secondary);font-size:10px;line-height:1.55;display:grid;grid-template-columns:16px minmax(0,1fr) auto;gap:7px;align-items:start}.ke-ask-error>svg{width:15px;height:15px;color:#d14b4b;margin-top:1px}.ke-ask-error p{margin:0}.ke-ask-error button{border:0;background:transparent;color:var(--accent-color);font-weight:600;display:flex;align-items:center;gap:4px;cursor:pointer}.ke-ask-error button svg{width:12px;height:12px}@keyframes ke-cursor-blink{50%{opacity:0}}
.ke-agent-message{width:100%;min-width:0}.ke-agent-message ::v-deep .message.assistant{width:100%;max-width:none;margin:0;padding:0}.ke-agent-message ::v-deep .message-content{min-width:0}.ke-agent-message ::v-deep .process-wrapper,.ke-agent-message ::v-deep .knowledge-citations{box-sizing:border-box}.ke-permission-note{margin:10px 0 0;color:var(--text-secondary);font-size:9px;line-height:1.5}.ke-create-perm{margin-top:16px;border:1px solid #e3e7e4;border-radius:10px;padding:12px;background:#fbfdfb}.ke-create-perm-toggle{display:grid;grid-template-columns:16px auto 1fr;gap:8px;align-items:start;cursor:pointer;color:#3d4640;font-size:12px}.ke-create-perm-toggle input{width:16px;height:16px;margin:1px 0 0;accent-color:var(--ke-accent);cursor:pointer}.ke-create-perm-toggle span{font-weight:600;white-space:nowrap}.ke-create-perm-toggle small{grid-column:2/4;color:#9ba19d;font-size:9px;line-height:1.5;white-space:normal}.ke-create-perm .ke-perm-toolbar{margin-top:12px}.ke-create-perm-tree{max-height:240px}
/* 弹窗内的搜索框：.ke-modal input 会给嵌套 input 误加边框/内边距，形成框中框，这里显式还原 */
.ke-modal .ke-search-box input{width:auto;min-width:0;flex:1;border:0;outline:0;background:transparent;padding:0;color:var(--text-primary);font-size:12px}
</style>
