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
          v-if="teamSpaceManagement.is_admin"
          class="ke-button admin-access"
          title="配置人员、部门及团队空间创建与管理权限"
          @click="$emit('manage-personnel')"
        ><IconUserRoundCog />人员与权限</button>
        <span class="ke-health" :class="healthClass"><i></i>{{ healthLabel }}</span>
        <button class="ke-button quiet" @click="showOperations = true"><IconActivity />任务</button>
        <button class="ke-button primary" @click="openCreateBase"><IconPlus />新建知识库</button>
      </div>
    </header>

    <div v-if="!capabilities.loaded" class="ke-loading-page">
      <span class="ke-spinner"></span><p>正在连接知识服务…</p>
    </div>
    <div v-else-if="!capabilities.enabled" class="ke-empty-page">
      <div class="ke-empty-illustration"><IconLibrary /></div>
      <h2>知识库未启用</h2>
      <p>{{ disabledMessage }}</p>
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
              <button v-if="selectedBase.visibility !== 'personal' && can('manage_permissions')" class="ke-button quiet" @click="openPermissions"><IconUserRoundCog />成员</button>
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
                  <option value="pending">待处理</option>
                  <option value="processing">解析中</option>
                  <option value="ready">已就绪</option>
                  <option value="failed">失败</option>
                </select>
              </label>
              <div class="ke-view-switch" aria-label="资料视图">
                <button :class="{ active: viewMode === 'grid' }" title="卡片视图" @click="viewMode = 'grid'"><IconLayoutGrid /></button>
                <button :class="{ active: viewMode === 'list' }" title="列表视图" @click="viewMode = 'list'"><IconList /></button>
              </div>
              <label v-if="can('upload')" class="ke-button primary upload">
                <IconUpload />导入资料
                <input type="file" multiple hidden @change="handleFiles" />
              </label>
            </div>
          </section>

          <section v-if="visibleUploadQueue.length" class="ke-upload-queue">
            <header><strong>导入进度</strong><span>{{ visibleUploadQueue.length }} 个文件</span></header>
            <div v-for="item in visibleUploadQueue" :key="item.id" class="ke-upload-item">
              <span class="ke-mini-file"><IconFileText /></span>
              <div><strong>{{ item.name }}</strong><div class="ke-progress"><i :style="{ width: `${item.progress}%` }"></i></div></div>
              <b :class="item.state">{{ uploadStateLabel(item) }}</b>
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

            <div v-if="loadingDocuments && !documents.length" class="ke-document-skeletons">
              <span v-for="index in 6" :key="index"></span>
            </div>

            <template v-else-if="documents.length">
              <article v-for="doc in documents" :key="doc.id" class="ke-document-card">
                <button class="ke-doc-open" :disabled="!previewable(doc)" title="双击预览" @dblclick="previewable(doc) && previewDocument(doc)">
                  <span class="ke-file-icon"><FileIcon :filename="doc.name" :size="28" /></span>
                  <span class="ke-document-copy">
                    <strong :title="doc.name">{{ doc.name }}</strong>
                    <small>
                      {{ formatSize(doc.size_bytes) }} · {{ formatDate(doc.updated_at) }}
                      <template v-if="doc.original_available"> · 原文已存</template>
                    </small>
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
                  <button v-if="doc.allowed_actions.includes('delete_document')" class="danger-text" title="删除" @click="removeDocument(doc)"><IconTrash2 /><span>删除</span></button>
                </div>
              </article>
            </template>

            <div v-else-if="!currentFolders.length || search || statusFilter" class="ke-empty-documents">
              <div><IconFiles /></div>
              <h3>{{ search || statusFilter ? '没有找到匹配资料' : '添加第一篇资料' }}</h3>
              <p>{{ search || statusFilter ? '换个关键词或筛选条件试试。' : '支持批量选择文件，也可以直接拖拽到这里。' }}</p>
              <label v-if="can('upload') && !search && !statusFilter" class="ke-button primary upload"><IconUpload />导入资料<input type="file" multiple hidden @change="handleFiles" /></label>
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
          <button v-if="asking" class="ke-icon-button subtle ke-stop-button" aria-label="停止回答" title="停止回答" @click="stopQuestion('user')"><IconSquare /></button>
          <button class="ke-icon-button subtle" aria-label="收起问答" title="收起问答" @click="askOpen = false"><IconPanelRightClose /></button>
        </header>

        <div class="ke-answer">
          <div v-if="!agentMessage && !asking" class="ke-answer-welcome">
            <div class="ke-answer-orb"><IconSparkles /></div>
            <h3>{{ selectedBase ? '从资料里找答案' : '先选择一个知识库' }}</h3>
            <p>{{ selectedBase ? '我会基于当前知识库回答，并标明引用来源。' : '选择后即可对资料进行提问、总结和对比。' }}</p>
            <div v-if="selectedBase" class="ke-question-suggestions">
              <button v-for="item in questionSuggestions" :key="item" @click="submitQuestion(item)">{{ item }}<IconArrowUpRight /></button>
            </div>
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

      <button v-else class="ke-ask-fab" :disabled="!selectedBase" @click="askOpen = true"><IconSparkles /><span>问问知识库</span></button>
    </div>

    <div v-if="showBaseModal" class="ke-modal-mask" @click.self="showBaseModal = false">
      <form class="ke-modal" @submit.prevent="saveBase">
        <header><div><h3>{{ baseModalMode === 'create' ? '新建知识库' : '知识库设置' }}</h3><p>{{ baseModalMode === 'create' ? '集中管理一组相关资料，并通过 AI 随时调用。' : '修改知识库的名称与说明。' }}</p></div><button type="button" aria-label="关闭" @click="showBaseModal = false"><IconX /></button></header>
        <label>名称<input v-model="baseForm.name" maxlength="127" placeholder="例如：宏观研究资料库" required /></label>
        <label>简介<textarea v-model="baseForm.description" maxlength="2000" placeholder="说明这个知识库包含什么内容，以及适合怎样使用。"></textarea></label>
        <label v-if="baseModalMode === 'create'">空间
          <select v-model="baseForm.visibility"><option value="personal">个人空间</option><option value="team" :disabled="!teamSpaceManagement.can_create">团队空间{{ teamSpaceOptionSuffix(teamSpaceManagement) }}</option><option value="shared">共享空间</option></select>
          <small v-if="baseForm.visibility === 'team' && teamSpaceManagement.is_admin">admin 无需授权；请选择团队空间所属部门。</small>
          <small v-else-if="baseForm.visibility === 'team'">已授权管理员可创建和管理；本部门其他成员默认只读。</small>
          <small v-else>空间决定知识库的默认可见范围，创建后可通过成员权限精细控制。</small>
        </label>
        <label v-if="baseModalMode === 'create' && baseForm.visibility === 'team' && teamSpaceManagement.is_admin">所属部门
          <select v-model="baseForm.department_id" required>
            <option disabled value="">请选择部门</option>
            <option v-for="department in teamSpaceManagement.departments" :key="department.id" :value="department.id">{{ department.name || department.id }}</option>
          </select>
          <small>所选部门的在职成员将默认获得查看权限。</small>
        </label>
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
        <header><div><h3>成员与权限</h3><p>邀请用户或部门，共同使用「{{ selectedBase ? selectedBase.name : '' }}」。</p></div><button aria-label="关闭" @click="showPermissionModal = false"><IconX /></button></header>
        <div class="ke-subject-search">
          <select v-model="subjectType" @change="searchSubjects"><option value="user">用户</option><option value="department">部门</option></select>
          <label class="ke-search-box"><IconSearch /><input v-model="subjectQuery" placeholder="搜索用户名或部门" @input="scheduleSubjectSearch" /></label>
        </div>
        <div v-if="subjectResults.length" class="ke-subject-results">
          <button v-for="subject in subjectResults" :key="`${subject.subject_type}-${subject.subject_id}`" @click="addPermission(subject)"><span class="ke-subject-avatar"><IconUserRound v-if="subject.subject_type === 'user'" /><IconBuilding2 v-else /></span><span><strong>{{ subject.label }}</strong><small>{{ subject.secondary || subject.subject_id }}</small></span><IconPlus /></button>
        </div>
        <div class="ke-permission-heading"><strong>已有成员</strong><span>{{ permissionItems.length }}</span></div>
        <div class="ke-permission-list">
          <div v-for="(item, index) in permissionItems" :key="`${item.subject_type}-${item.subject_id}`">
            <span class="ke-subject-avatar"><IconUserRound v-if="item.subject_type === 'user'" /><IconBuilding2 v-else /></span>
            <span class="ke-permission-copy"><strong>{{ item.label || item.subject_id }}</strong><small>{{ item.subject_type === 'user' ? '用户' : '部门' }}</small></span>
            <select v-model="item.role" :disabled="!canEditPermissionRole(selectedBase, teamSpaceManagement)"><option value="viewer">查看者</option><option value="maintainer">维护者</option><option value="manager">管理员</option></select>
            <button class="ke-icon-button danger-hover" title="移除" :disabled="!canRemovePermission(selectedBase, teamSpaceManagement, item)" @click="permissionItems.splice(index, 1)"><IconTrash2 /></button>
          </div>
          <p v-if="!permissionItems.length">尚未添加授权成员</p>
        </div>
        <p v-if="selectedBase && selectedBase.visibility === 'team' && !teamSpaceManagement.is_admin" class="ke-permission-note">团队库的维护者和管理员角色由 admin 配置。</p>
        <footer><button class="ke-button quiet" @click="showPermissionModal = false">取消</button><button class="ke-button primary" :disabled="saving" @click="savePermissions">{{ saving ? '保存中…' : '保存权限' }}</button></footer>
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

    <div v-if="showOperations" class="ke-drawer-mask" @click.self="showOperations = false">
      <aside class="ke-drawer">
        <header><div><h2>任务中心</h2><p>查看资料导入与解析进度</p></div><button aria-label="关闭" @click="showOperations = false"><IconX /></button></header>
        <button class="ke-button quiet refresh" @click="loadOperations"><IconRefreshCw />刷新</button>
        <div v-for="operation in operations" :key="operation.id" class="ke-operation">
          <div><span class="ke-operation-icon"><IconFileClock /></span><div><strong>{{ operationLabel(operation.type) }}</strong><small>{{ formatDate(operation.updated_at) }}</small></div><span class="ke-status" :class="operation.status"><i></i>{{ operationStatusLabel(operation.status) }}</span></div>
          <div class="ke-progress"><i :style="{ width: `${Math.round((operation.progress || 0) * 100)}%` }"></i></div>
          <p v-if="operation.error">{{ operation.error.message }}</p>
        </div>
        <div v-if="!operations.length" class="ke-empty-small"><IconActivity /><span>暂无任务</span></div>
      </aside>
    </div>

    <transition name="ke-toast"><div v-if="toast" class="ke-toast" :class="toast.type"><IconCircleCheck v-if="toast.type !== 'error'" /><IconCircleAlert v-else />{{ toast.message }}</div></transition>
  </section>
</template>

<script>
import { IconActivity, IconArrowLeft, IconArrowUp, IconArrowUpRight, IconBookDashed, IconBookOpen, IconBuilding2, IconChevronDown, IconChevronRight, IconCircleAlert, IconCircleCheck, IconClock3, IconDatabase, IconDownload, IconEye, IconFileClock, IconFileText, IconFiles, IconFolder, IconFolderPlus, IconLayoutGrid, IconLibrary, IconList, IconListFilter, IconPanelRightClose, IconPencil, IconPlus, IconRefreshCw, IconSearch, IconSettings2, IconShieldCheck, IconSparkles, IconSquare, IconTrash2, IconTriangleAlert, IconUpload, IconUploadCloud, IconUserRound, IconUserRoundCog, IconUsers, IconX } from './icons.js'
import DocxPreview from '../../components/DocxPreview.vue'
import ExcelPreview from '../../components/ExcelPreview.vue'
import FileIcon from '../../components/FileIcon.vue'
import ChatMessage from '../../components/ChatMessage.vue'
import { attachStream, cancelMessage, sendMessage } from '../../api/chat.js'
import { processingElapsedText } from './utils/presentation.js'
import {
  canEditPermissionRole as canEditPermissionRoleUtil,
  canRemovePermission as canRemovePermissionUtil,
  defaultTeamDepartmentId,
  teamSpaceEmptyHint as teamSpaceEmptyHintUtil,
  teamSpaceOptionSuffix as teamSpaceOptionSuffixUtil,
} from './utils/teamSpace.js'
import {
  createOptimisticUploadDocument,
  mergeServerDocumentsWithOptimistic,
} from './utils/uploadPresentation.js'
import {
  createFolder, createKnowledgeBase, deleteDocument, deleteFolder,
  deleteKnowledgeBase, getDocumentBlob, getKnowledgeStatus,
  listDocuments, listFolders, listKnowledgeBases, listKnowledgeOperations, listPermissions,
  moveDocument, prepareKnowledgeChatSession, replacePermissions, retryDocument, searchPermissionSubjects, updateFolder,
  updateKnowledgeBase, uploadDocument,
} from './api.js'

function clampLayout(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function storedLayoutNumber(key, fallback) {
  const value = Number(localStorage.getItem(key))
  return Number.isFinite(value) ? value : fallback
}

export default {
  name: 'KnowledgeWorkbench',
  components: {
    ChatMessage,
    DocxPreview,
    ExcelPreview,
    FileIcon,
    IconActivity,
    IconArrowLeft,
    IconArrowUp,
    IconArrowUpRight,
    IconBookDashed,
    IconBookOpen,
    IconBuilding2,
    IconChevronDown,
    IconChevronRight,
    IconCircleAlert,
    IconCircleCheck,
    IconClock3,
    IconDatabase,
    IconDownload,
    IconEye,
    IconFileClock,
    IconFiles,
    IconFileText,
    IconFolder,
    IconFolderPlus,
    IconLayoutGrid,
    IconLibrary,
    IconList,
    IconListFilter,
    IconPanelRightClose,
    IconPencil,
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
  emits: ['close', 'manage-personnel'],
  data() {
    return {
      capabilities: { loaded: false, enabled: false },
      teamSpaceManagement: { can_create: false, can_manage: false, department_id: null, is_admin: false, departments: [] },
      health: null,
      bases: [],
      baseQuery: '',
      selectedSpace: 'personal',
      selectedBaseId: '',
      folders: [],
      selectedFolderId: '',
      documents: [],
      documentsPage: { total: 0 },
      loadingDocuments: false,
      search: '',
      statusFilter: '',
      viewMode: localStorage.getItem('ke-view-mode') || 'list',
      dragging: false,
      uploadQueue: [],
      operations: [],
      showOperations: false,
      askOpen: true,
      libraryWidth: clampLayout(storedLayoutNumber('easyagent.layout.knowledge.library', 252), 200, 420),
      askWidth: clampLayout(storedLayoutNumber('easyagent.layout.knowledge.ask', 344), 280, 560),
      question: '',
      lastQuestion: '',
      agentMessage: null,
      asking: false,
      askError: '',
      showBaseModal: false,
      baseModalMode: 'create',
      showFolderModal: false,
      showMoveModal: false,
      movingDocument: null,
      moveTargetFolderId: '',
      showPermissionModal: false,
      saving: false,
      baseForm: { name: '', description: '', visibility: 'personal', department_id: '' },
      folderForm: { id: '', name: '', parentId: '' },
      confirmation: { open: false, title: '', description: '', confirmLabel: '确认删除', tone: 'danger' },
      permissionItems: [],
      subjectType: 'user',
      subjectQuery: '',
      subjectResults: [],
      toast: null,
      preview: { open: false, loading: false, name: '', kind: '', url: '', text: '', buffer: null },
      statusUnauthorized: false,
      activeKnowledgeResize: null,
      askController: null,
      askGeneration: 0,
      activeChatSessionId: '',
      streamDone: false,
      blockOrder: 0,
      pollTimer: null,
      toastTimer: null,
      searchTimer: null,
      confirmResolver: null,
      documentRequestSequence: 0,
    }
  },
  computed: {
    spaces() {
      return [
        { value: 'personal', label: '个人空间', description: '仅自己可见', icon: IconUserRound },
        { value: 'team', label: '团队空间', description: '本部门默认可见', icon: IconUsers },
        { value: 'shared', label: '共享空间', description: '按成员或部门授权', icon: IconLibrary },
      ]
    },
    questionSuggestions() {
      return ['概括这批资料的核心结论', '提取重要数据与时间点', '不同报告有哪些观点分歧？']
    },
    knowledgeLayoutStyle() {
      return {
        '--ke-library-width': `${this.libraryWidth}px`,
        '--ke-ask-width': `${this.askWidth}px`,
      }
    },
    disabledMessage() {
      if (this.statusUnauthorized) return '当前账号无权访问知识库，请联系管理员确认权限。'
      return '知识服务尚未启用，主聊天仍可正常使用。请由管理员完成知识服务配置后再试。'
    },
    selectedBase() {
      return this.bases.find(item => item.id === this.selectedBaseId) || null
    },
    currentFolders() {
      return this.folders.filter(item => (item.parent_id || '') === this.selectedFolderId)
    },
    currentFolder() {
      return this.folders.find(item => item.id === this.selectedFolderId) || null
    },
    visibleUploadQueue() {
      return this.uploadQueue.filter(item => item.baseId === this.selectedBaseId && item.state !== 'ready')
    },
    folderBreadcrumbs() {
      const chain = []
      const visited = new Set()
      let cursor = this.currentFolder
      while (cursor && !visited.has(cursor.id)) {
        visited.add(cursor.id)
        chain.unshift(cursor)
        cursor = this.folders.find(item => item.id === cursor.parent_id)
      }
      return chain
    },
    folderOptions() {
      return this.folders
        .map(folder => ({ ...folder, path: this.folderPath(folder) }))
        .sort((a, b) => a.path.localeCompare(b.path, 'zh-CN'))
    },
    healthClass() {
      return this.health && this.health.ready ? 'ready' : 'degraded'
    },
    healthLabel() {
      return this.health && this.health.ready ? '知识服务正常' : '知识服务待就绪'
    },
    hasActiveWork() {
      return this.documents.some(item => !item.optimistic && ['pending', 'processing', 'deleting'].includes(item.status))
    },
    hasCurrentUploadRequest() {
      return this.uploadQueue.some(item => item.baseId === this.selectedBaseId && ['uploading', 'saving'].includes(item.state))
    },
  },
  watch: {
    hasActiveWork(active) {
      active ? this.startPolling() : this.stopPolling()
    },
    viewMode(value) {
      localStorage.setItem('ke-view-mode', value)
    },
  },
  mounted() {
    this.initialize()
  },
  beforeDestroy() {
    this.stopQuestion('unmount')
    this.finishKnowledgeResize()
    this.stopPolling()
    this.closePreview()
    clearTimeout(this.toastTimer)
    clearTimeout(this.searchTimer)
    this.resolveConfirmation(false)
  },
  methods: {
    // ── 布局拖拽 ────────────────────────────────────────────────────
    finishKnowledgeResize() {
      if (!this.activeKnowledgeResize) return
      localStorage.setItem('easyagent.layout.knowledge.library', String(this.libraryWidth))
      localStorage.setItem('easyagent.layout.knowledge.ask', String(this.askWidth))
      this.activeKnowledgeResize = null
      document.body.classList.remove('ke-layout-resizing')
      window.removeEventListener('pointermove', this.handleKnowledgeResize)
      window.removeEventListener('pointerup', this.finishKnowledgeResize)
      window.removeEventListener('pointercancel', this.finishKnowledgeResize)
    },
    handleKnowledgeResize(event) {
      if (!this.activeKnowledgeResize) return
      const delta = event.clientX - this.activeKnowledgeResize.startX
      if (this.activeKnowledgeResize.kind === 'library') {
        this.libraryWidth = clampLayout(this.activeKnowledgeResize.startWidth + delta, 200, 420)
      } else {
        this.askWidth = clampLayout(this.activeKnowledgeResize.startWidth - delta, 280, 560)
      }
    },
    startKnowledgeResize(kind, event) {
      if (event.pointerType === 'mouse' && event.button !== 0) return
      this.activeKnowledgeResize = {
        kind,
        startX: event.clientX,
        startWidth: kind === 'library' ? this.libraryWidth : this.askWidth,
      }
      document.body.classList.add('ke-layout-resizing')
      window.addEventListener('pointermove', this.handleKnowledgeResize)
      window.addEventListener('pointerup', this.finishKnowledgeResize)
      window.addEventListener('pointercancel', this.finishKnowledgeResize)
      event.preventDefault()
    },
    resizeKnowledgeWithKeyboard(kind, event) {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      const direction = event.key === 'ArrowRight' ? 1 : -1
      if (kind === 'library') {
        this.libraryWidth = clampLayout(this.libraryWidth + direction * 16, 200, 420)
        localStorage.setItem('easyagent.layout.knowledge.library', String(this.libraryWidth))
      } else {
        this.askWidth = clampLayout(this.askWidth - direction * 16, 280, 560)
        localStorage.setItem('easyagent.layout.knowledge.ask', String(this.askWidth))
      }
      event.preventDefault()
    },

    // ── 展示辅助 ────────────────────────────────────────────────────
    can(action) {
      return Boolean(this.selectedBase && this.selectedBase.allowed_actions && this.selectedBase.allowed_actions.includes(action))
    },
    canCreateInSpace(space) {
      return space !== 'team' || this.teamSpaceManagement.can_create
    },
    baseCount(space) {
      return this.bases.filter(item => item.visibility === space).length
    },
    basesForSpace(space) {
      const keyword = this.baseQuery.trim().toLowerCase()
      return this.bases.filter(item => item.visibility === space && (!keyword || item.name.toLowerCase().includes(keyword) || (item.description || '').toLowerCase().includes(keyword)))
    },
    canEditPermissionRole(base, management) {
      return canEditPermissionRoleUtil(base, management)
    },
    canRemovePermission(base, management, permission) {
      return canRemovePermissionUtil(base, management, permission)
    },
    teamSpaceEmptyHint(management) {
      return teamSpaceEmptyHintUtil(management)
    },
    teamSpaceOptionSuffix(management) {
      return teamSpaceOptionSuffixUtil(management)
    },
    roleLabel(role) {
      return ({ viewer: '查看者', maintainer: '维护者', manager: '管理员' })[role] || role
    },
    spaceLabel(space) {
      return ({ personal: '个人空间', team: '团队空间', shared: '共享空间' })[space] || space
    },
    statusLabel(status) {
      return ({ pending: '待处理', processing: '解析中', ready: '已就绪', failed: '失败', cancelled: '已取消', deleting: '删除中', unknown: '未知' })[status] || status
    },
    operationStatusLabel(status) {
      return ({ accepted: '已受理', running: '进行中', succeeded: '成功', failed: '失败', partial_failed: '部分失败' })[status] || status
    },
    operationLabel(type) {
      return ({ dataset_create: '创建知识库', dataset_delete: '删除知识库', document_upload: '上传文档', document_parse: '解析文档', document_delete: '删除文档' })[type] || type
    },
    uploadStateLabel(item) {
      return item.state === 'failed' ? item.error || '失败' : item.state === 'ready' ? '解析完成' : item.state === 'processing' ? '等待解析' : item.state === 'saving' ? '正在保存原文' : `${item.progress}%`
    },
    formatSize(size = 0) {
      if (size < 1024) return `${size} B`
      if (size < 1048576) return `${(size / 1024).toFixed(1)} KB`
      return `${(size / 1048576).toFixed(1)} MB`
    },
    formatDate(value) {
      return value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'
    },
    folderPath(folder) {
      const parts = [folder.name]
      const visited = new Set([folder.id])
      let parent = this.folders.find(item => item.id === folder.parent_id)
      while (parent && !visited.has(parent.id)) {
        visited.add(parent.id)
        parts.unshift(parent.name)
        parent = this.folders.find(item => item.id === parent.parent_id)
      }
      return parts.join(' / ')
    },
    documentStatusText(doc) {
      if (doc.status !== 'processing') return this.statusLabel(doc.status)
      const progress = Number(doc.progress || 0)
      if (progress < 0.02) return '正在读取原文'
      if (progress < 0.85) return `正在建立索引 ${Math.max(1, Math.round(progress * 100))}%`
      return '正在完成索引'
    },
    processingElapsed(doc) {
      return processingElapsedText(doc, this.operations)
    },
    notify(message, type = 'success') {
      this.toast = { message, type }
      clearTimeout(this.toastTimer)
      this.toastTimer = setTimeout(() => { this.toast = null }, 3000)
    },
    handleError(error) {
      console.error(error)
      this.notify((error && error.message) || '操作失败', 'error')
    },

    // ── 初始化与加载 ────────────────────────────────────────────────
    async initialize() {
      try {
        const status = await getKnowledgeStatus()
        this.health = status || null
        Object.assign(this.capabilities, { loaded: true, enabled: Boolean(status && status.enabled) })
        if (!this.capabilities.enabled) return
        await Promise.all([this.loadBases(), this.loadHealth(), this.loadOperations()])
      } catch (error) {
        this.capabilities.loaded = true
        const httpStatus = error && error.response ? error.response.status : 0
        if (httpStatus === 401 || httpStatus === 403) {
          this.statusUnauthorized = true
          return
        }
        this.handleError(error)
      }
    },
    async loadHealth() {
      try { this.health = await getKnowledgeStatus() } catch (_) { this.health = { ready: false } }
    },
    async loadBases(preferredId = '') {
      const response = await listKnowledgeBases({ pageSize: 100 })
      this.bases = response.items || []
      Object.assign(
        this.teamSpaceManagement,
        { can_create: false, can_manage: false, department_id: null, is_admin: false, departments: [] },
        response.team_space_management || {}
      )
      const target = preferredId || this.selectedBaseId
      if (target && this.bases.some(item => item.id === target)) await this.selectBase(target)
      else if (this.basesForSpace(this.selectedSpace).length) await this.selectBase(this.basesForSpace(this.selectedSpace)[0].id)
      else { this.selectedBaseId = ''; this.folders = []; this.documents = [] }
    },
    async selectSpace(space) {
      this.selectedSpace = space
      if (this.selectedBase && this.selectedBase.visibility === space) return
      const first = this.basesForSpace(space)[0]
      if (first) await this.selectBase(first.id)
      else { this.selectedBaseId = ''; this.selectedFolderId = ''; this.folders = []; this.documents = [] }
    },
    async selectBase(baseId) {
      if (this.asking && this.selectedBaseId !== baseId) this.stopQuestion('switch')
      if (this.selectedBaseId !== baseId) { this.documents = []; this.folders = [] }
      this.selectedBaseId = baseId
      this.selectedFolderId = ''
      this.agentMessage = null
      this.askError = ''
      await Promise.all([this.loadFolders(), this.loadDocuments({ initial: true })])
      await this.loadOperations()
    },
    async selectFolder(folderId) {
      this.selectedFolderId = folderId
      await this.loadDocuments()
    },
    async goParentFolder() {
      await this.selectFolder(this.currentFolder ? (this.currentFolder.parent_id || '') : '')
    },
    async loadFolders() {
      const baseId = this.selectedBaseId
      if (!baseId) return
      try {
        const response = await listFolders(baseId)
        if (this.selectedBaseId === baseId) this.folders = response.items || []
      } catch (error) { this.handleError(error) }
    },
    async loadDocuments({ initial = false } = {}) {
      const baseId = this.selectedBaseId
      const folderId = this.selectedFolderId
      if (!baseId) return
      const requestSequence = ++this.documentRequestSequence
      this.loadingDocuments = initial || this.documents.length === 0
      try {
        const query = this.search.trim()
        const response = await listDocuments(baseId, { pageSize: 100, folderId, directOnly: !query, status: this.statusFilter, q: query })
        if (requestSequence !== this.documentRequestSequence || this.selectedBaseId !== baseId || this.selectedFolderId !== folderId) return
        const serverDocuments = response.items || []
        this.documents = mergeServerDocumentsWithOptimistic(
          serverDocuments,
          this.documents,
          { baseId, folderId, preserve: !query && !this.statusFilter }
        )
        this.documentsPage = response.page || { total: serverDocuments.length }
        this.syncUploadQueue()
        await this.loadFolders()
      } catch (error) { this.handleError(error) } finally {
        if (requestSequence === this.documentRequestSequence) this.loadingDocuments = false
      }
    },
    clearFilters() {
      this.search = ''
      this.statusFilter = ''
      this.loadDocuments()
    },
    async loadOperations() {
      try { this.operations = (await listKnowledgeOperations({ pageSize: 100 })).items || [] } catch (error) { this.handleError(error) }
    },

    // ── 知识库 CRUD ─────────────────────────────────────────────────
    openCreateBase() {
      const visibility = this.canCreateInSpace(this.selectedSpace) ? this.selectedSpace : 'personal'
      Object.assign(this.baseForm, {
        name: '',
        description: '',
        visibility,
        department_id: defaultTeamDepartmentId(this.teamSpaceManagement),
      })
      this.baseModalMode = 'create'
      this.showBaseModal = true
    },
    editBase() {
      Object.assign(this.baseForm, {
        name: this.selectedBase.name,
        description: this.selectedBase.description || '',
        visibility: this.selectedBase.visibility,
        department_id: '',
      })
      this.baseModalMode = 'edit'
      this.showBaseModal = true
    },
    async saveBase() {
      this.saving = true
      try {
        if (this.baseModalMode === 'create') {
          const payload = {
            name: this.baseForm.name.trim(),
            description: this.baseForm.description || '',
            visibility: this.baseForm.visibility,
          }
          if (this.baseForm.visibility === 'team') payload.department_id = this.baseForm.department_id || null
          const created = await createKnowledgeBase(payload)
          this.selectedSpace = created.visibility
          await this.loadBases(created.id)
          this.notify('知识库已创建')
        } else {
          await updateKnowledgeBase(this.selectedBaseId, { name: this.baseForm.name.trim(), description: this.baseForm.description || '' })
          await this.loadBases(this.selectedBaseId)
          this.notify('知识库已更新')
        }
        this.showBaseModal = false
      } catch (error) { this.handleError(error) } finally { this.saving = false }
    },
    async removeBase() {
      const confirmed = await this.askConfirmation({ title: '删除这个知识库？', description: `「${this.selectedBase.name}」及其中全部资料将被删除，此操作无法撤销。`, confirmLabel: '删除知识库' })
      if (!confirmed) return
      try {
        await deleteKnowledgeBase(this.selectedBaseId)
        this.selectedBaseId = ''
        await this.loadBases()
        this.notify('知识库已删除')
      } catch (error) { this.handleError(error) }
    },

    // ── 文件夹 ──────────────────────────────────────────────────────
    addFolder() {
      Object.assign(this.folderForm, { id: '', name: '', parentId: this.selectedFolderId })
      this.showFolderModal = true
    },
    renameCurrentFolder() {
      const folder = this.currentFolder
      if (!folder) return
      Object.assign(this.folderForm, { id: folder.id, name: folder.name, parentId: folder.parent_id || '' })
      this.showFolderModal = true
    },
    async saveFolder() {
      if (!this.folderForm.name.trim()) return
      this.saving = true
      try {
        if (this.folderForm.id) { await updateFolder(this.folderForm.id, this.folderForm.name.trim()); this.notify('文件夹已重命名') }
        else { await createFolder(this.selectedBaseId, this.folderForm.name.trim(), this.folderForm.parentId); this.notify('文件夹已创建') }
        this.showFolderModal = false
        await this.loadFolders()
      } catch (error) { this.handleError(error) } finally { this.saving = false }
    },
    async removeFolder() {
      const folder = this.folders.find(item => item.id === this.selectedFolderId)
      if (!folder || !await this.askConfirmation({ title: '删除这个文件夹？', description: `仅空文件夹「${folder.name}」可以被删除。`, confirmLabel: '删除文件夹' })) return
      try {
        const parentId = folder.parent_id || ''
        await deleteFolder(folder.id)
        this.selectedFolderId = parentId
        await Promise.all([this.loadFolders(), this.loadDocuments()])
        this.notify('文件夹已删除')
      } catch (error) { this.handleError(error) }
    },

    // ── 上传 ────────────────────────────────────────────────────────
    async handleFiles(event) {
      const files = Array.from(event.target.files || [])
      event.target.value = ''
      await this.uploadFiles(files)
    },
    async handleDrop(event) {
      this.dragging = false
      if (!this.selectedBase || !this.can('upload')) return
      await this.uploadFiles(Array.from((event.dataTransfer && event.dataTransfer.files) || []))
    },
    async uploadFiles(files) {
      const uploadBaseId = this.selectedBaseId
      const uploadFolderId = this.selectedFolderId
      if (!uploadBaseId) return
      const pendingUploads = files.map(file => {
        const queueId = crypto.randomUUID()
        const item = { id: queueId, optimisticId: `upload-${queueId}`, baseId: uploadBaseId, folderId: uploadFolderId, documentId: '', name: file.name, progress: 0, state: 'uploading', error: '' }
        this.uploadQueue.push(item)
        if (this.selectedBaseId === uploadBaseId && this.selectedFolderId === uploadFolderId && !this.search.trim() && !this.statusFilter) {
          this.documents = [createOptimisticUploadDocument(file, item), ...this.documents]
          this.documentsPage = { ...this.documentsPage, total: Number(this.documentsPage.total || 0) + 1 }
        }
        return { file, item }
      })
      for (const { file, item } of pendingUploads) {
        try {
          const result = await uploadDocument(uploadBaseId, file, {
            folderId: uploadFolderId,
            onProgress: value => {
              if (value >= 100) { item.progress = 95; item.state = 'saving' }
              else item.progress = Math.min(value, 95)
            },
          })
          item.documentId = result.document.id
          item.progress = 100
          item.state = 'processing'
          const optimisticIndex = this.documents.findIndex(doc => doc.id === item.optimisticId)
          if (optimisticIndex >= 0) this.documents.splice(optimisticIndex, 1, { ...result.document, optimistic: false })
        } catch (error) {
          const optimisticIndex = this.documents.findIndex(doc => doc.id === item.optimisticId)
          if (optimisticIndex >= 0) {
            this.documents.splice(optimisticIndex, 1)
            this.documentsPage = { ...this.documentsPage, total: Math.max(0, Number(this.documentsPage.total || 1) - 1) }
          }
          item.state = 'failed'
          item.error = error.message
          this.handleError(error)
        }
      }
      if (this.selectedBaseId === uploadBaseId) await this.loadDocuments()
      await this.loadOperations()
    },
    syncUploadQueue() {
      for (const item of this.uploadQueue) {
        if (item.baseId !== this.selectedBaseId) continue
        const document = this.documents.find(doc => doc.id === item.documentId)
        if (!document) continue
        if (document.status === 'ready') { item.state = 'ready'; item.progress = 100 }
        else if (document.status === 'failed') { item.state = 'failed'; item.error = (document.error && document.error.message) || '解析失败' }
      }
      this.uploadQueue = this.uploadQueue.filter(item => item.state !== 'ready')
    },

    // ── 文档操作 ────────────────────────────────────────────────────
    async moveDoc(doc, folderId) {
      try {
        await moveDocument(doc.id, folderId)
        await Promise.all([this.loadDocuments(), this.loadFolders()])
        this.notify('文档已移动')
      } catch (error) { this.handleError(error) }
    },
    openMoveDocument(doc) {
      this.movingDocument = doc
      this.moveTargetFolderId = doc.folder_id || ''
      this.showMoveModal = true
    },
    async confirmMoveDocument() {
      if (!this.movingDocument) return
      this.saving = true
      try {
        await this.moveDoc(this.movingDocument, this.moveTargetFolderId)
        this.showMoveModal = false
      } finally { this.saving = false }
    },
    async retryDoc(doc) {
      try {
        await retryDocument(doc.id)
        await Promise.all([this.loadDocuments(), this.loadOperations()])
        this.notify('已重新提交解析')
      } catch (error) { this.handleError(error) }
    },
    async removeDocument(doc) {
      if (!await this.askConfirmation({ title: '删除这篇资料？', description: `「${doc.name}」将从知识库中永久移除。`, confirmLabel: '删除资料' })) return
      try {
        await deleteDocument(doc.id)
        await Promise.all([this.loadDocuments(), this.loadOperations()])
        this.notify('文档已删除')
      } catch (error) { this.handleError(error) }
    },
    previewable(doc) {
      return !doc.optimistic && (/^(application\/pdf|text\/|image\/)/.test(doc.content_type || '') || /\.(md|txt|docx|xlsx?|pptx)$/i.test(doc.name || ''))
    },
    async previewDocument(doc) {
      this.closePreview()
      Object.assign(this.preview, { open: true, loading: true, name: doc.name, kind: '', url: '', text: '' })
      try {
        const { blob } = await getDocumentBlob(doc.id, 'inline')
        if ((doc.content_type || '').startsWith('text/') || /\.(md|txt)$/i.test(doc.name)) {
          this.preview.kind = 'text'
          this.preview.text = await blob.text()
        } else {
          this.preview.url = URL.createObjectURL(blob)
          if (/\.xlsx?$/i.test(doc.name)) this.preview.kind = 'excel'
          else this.preview.kind = /\.docx$/i.test(doc.name) ? 'docx' : /\.pptx$/i.test(doc.name) ? 'pptx' : (doc.content_type || '').startsWith('image/') ? 'image' : 'frame'
        }
      } catch (error) { this.closePreview(); this.handleError(error) } finally { this.preview.loading = false }
    },
    closePreview() {
      if (this.preview.url) URL.revokeObjectURL(this.preview.url)
      Object.assign(this.preview, { open: false, loading: false, name: '', kind: '', url: '', text: '', buffer: null })
    },
    async downloadDocument(doc) {
      try {
        this.notify('正在准备下载…')
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
        this.notify('已开始下载')
      } catch (error) { this.handleError(error) }
    },

    // ── 权限 ────────────────────────────────────────────────────────
    async openPermissions() {
      this.showPermissionModal = true
      this.subjectResults = []
      try {
        const response = await listPermissions(this.selectedBaseId)
        this.permissionItems = (response.items || []).map(item => ({ ...item, label: item.label || item.subject_id }))
      } catch (error) { this.handleError(error) }
    },
    scheduleSubjectSearch() {
      clearTimeout(this.searchTimer)
      this.searchTimer = setTimeout(this.searchSubjects, 250)
    },
    async searchSubjects() {
      if (!this.showPermissionModal) return
      try {
        const response = await searchPermissionSubjects(this.selectedBaseId, this.subjectType, this.subjectQuery.trim())
        this.subjectResults = response.items || []
      } catch (error) { this.handleError(error) }
    },
    addPermission(subject) {
      if (!this.permissionItems.some(item => item.subject_type === subject.subject_type && item.subject_id === subject.subject_id)) {
        this.permissionItems.push({ ...subject, role: 'viewer' })
      }
    },
    async savePermissions() {
      this.saving = true
      try {
        await replacePermissions(this.selectedBaseId, this.permissionItems.map(({ subject_type, subject_id, role }) => ({ subject_type, subject_id, role })))
        this.showPermissionModal = false
        this.notify('权限已保存')
      } catch (error) { this.handleError(error) } finally { this.saving = false }
    },

    // ── 知识库问答 ──────────────────────────────────────────────────
    touchAgentMessage() {
      if (!this.agentMessage) return
      this.agentMessage = {
        ...this.agentMessage,
        blocks: [...(this.agentMessage.blocks || [])],
      }
    },
    findAgentBlock(type, event) {
      if (!this.agentMessage) return null
      const step = event.step || 0
      if (type === 'tool_call') {
        const callId = event.tool_call_id || `tool-${event.tool_name || ''}`
        return this.agentMessage.blocks.find(
          block => block.type === type && (block.id === callId || block.tool_call_id === callId)
        ) || null
      }
      return this.agentMessage.blocks.find(block => block.type === type && (block.step || 0) === step) || null
    },
    ensureAgentBlock(type, event) {
      let block = this.findAgentBlock(type, event)
      if (block) return block
      this.blockOrder += 1
      block = { type, content: '', step: event.step || 0, order: this.blockOrder }
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
      this.agentMessage.blocks.push(block)
      return block
    },
    applyAgentEvent(event, baseId, generation) {
      if (this.selectedBaseId !== baseId || generation !== this.askGeneration || !this.agentMessage) return

      if (event.type === 'knowledge_evidence') {
        this.agentMessage.knowledge_evidence = Array.isArray(event.evidence) ? event.evidence : []
        this.agentMessage.knowledge_warnings = Array.isArray(event.warnings) ? event.warnings : []
      } else if (event.type === 'thinking_start') {
        this.ensureAgentBlock('thinking', event)
      } else if (event.type === 'thinking') {
        const block = this.ensureAgentBlock('thinking', event)
        block.content = event.full_content || `${block.content || ''}${event.content || ''}`
        this.agentMessage.thinking = this.agentMessage.blocks
          .filter(item => item.type === 'thinking')
          .map(item => item.content || '')
          .join('')
      } else if (event.type === 'thinking_end') {
        const block = this.ensureAgentBlock('thinking', event)
        this.$set(block, 'duration', event.duration || 0)
      } else if (event.type === 'content_start') {
        this.ensureAgentBlock('content', event)
      } else if (event.type === 'content' && event.content) {
        const block = this.ensureAgentBlock('content', event)
        block.content = `${block.content || ''}${event.content}`
        this.agentMessage.content = `${this.agentMessage.content || ''}${event.content}`
      } else if (event.type === 'tool_call') {
        const block = this.ensureAgentBlock('tool_call', event)
        block.arguments = event.arguments || block.arguments || {}
      } else if (event.type === 'tool_result') {
        const block = this.ensureAgentBlock('tool_call', event)
        block.result = typeof event.result === 'string' ? event.result : JSON.stringify(event.result || '')
        block.success = event.success !== false
        this.$set(block, 'duration', event.duration || 0)
      } else if (event.type === 'done') {
        this.streamDone = true
        this.agentMessage.loading = false
        if (event.content) this.agentMessage.content = event.content
        for (const block of this.agentMessage.blocks) {
          if (block.type === 'thinking' && block.duration == null) this.$set(block, 'duration', 0)
        }
      } else if (event.type === 'error') {
        this.streamDone = true
        this.agentMessage.loading = false
        this.$set(this.agentMessage, 'error', event.content || '处理失败')
        this.askError = this.agentMessage.error
      }
      this.touchAgentMessage()
    },
    async stopQuestion(reason = 'user') {
      const controller = this.askController
      const sessionId = this.activeChatSessionId
      if (!controller && !this.asking) return
      this.askGeneration += 1
      this.askController = null
      this.activeChatSessionId = ''
      this.asking = false
      if (controller) controller.abort()
      if (this.agentMessage) this.agentMessage = { ...this.agentMessage, loading: false }
      if (sessionId) {
        try { await cancelMessage(sessionId) } catch (_) { /* 中止以本地状态为准 */ }
      }
      if (reason === 'user') this.notify('已停止回答')
    },
    retryQuestion() {
      if (!this.lastQuestion || this.asking) return
      this.question = this.lastQuestion
      this.submitQuestion()
    },
    async submitQuestion(suggested = '') {
      if (suggested) this.question = suggested
      if (!this.question.trim() || !this.selectedBaseId || this.asking) return
      const baseId = this.selectedBaseId
      const generation = ++this.askGeneration
      this.askController = new AbortController()
      this.asking = true
      this.askError = ''
      this.streamDone = false
      this.blockOrder = 0
      this.agentMessage = {
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
      this.lastQuestion = this.question.trim()
      try {
        const prepared = await prepareKnowledgeChatSession(baseId)
        if (generation !== this.askGeneration) return
        this.activeChatSessionId = prepared.session_id
        const onChunk = event => this.applyAgentEvent(event, baseId, generation)
        const signal = this.askController ? this.askController.signal : undefined
        await sendMessage(prepared.session_id, this.lastQuestion, onChunk, signal, true)
        // 与新对话一样，SSE 连接意外结束时挂载到后台流并回放已有事件。
        if (!this.streamDone && generation === this.askGeneration && this.askController && !this.askController.signal.aborted) {
          await attachStream(prepared.session_id, onChunk, this.askController.signal)
        }
        if (generation === this.askGeneration) this.question = ''
      } catch (error) {
        if (error && error.name !== 'AbortError' && generation === this.askGeneration) {
          this.askError = (error && error.message) || '知识库问答失败'
          if (this.agentMessage) this.agentMessage = { ...this.agentMessage, loading: false, error: this.askError }
          this.handleError(error)
        }
      } finally {
        if (generation === this.askGeneration) {
          this.askController = null
          this.activeChatSessionId = ''
          this.asking = false
          if (this.agentMessage) this.agentMessage = { ...this.agentMessage, loading: false }
        }
      }
    },

    // ── 确认框 / 轮询 ───────────────────────────────────────────────
    askConfirmation(options) {
      Object.assign(this.confirmation, { open: true, tone: 'danger', ...options })
      return new Promise(resolve => { this.confirmResolver = resolve })
    },
    resolveConfirmation(value) {
      this.confirmation.open = false
      if (this.confirmResolver) this.confirmResolver(value)
      this.confirmResolver = null
    },
    startPolling() {
      if (this.pollTimer) return
      this.pollTimer = setInterval(async () => {
        if (document.hidden || !this.selectedBaseId) return
        // The upload endpoint creates its database record before returning. Refreshing
        // the list in that window can display both the server row and our optimistic row.
        if (!this.hasCurrentUploadRequest) await this.loadDocuments()
        await this.loadOperations()
      }, 3000)
    },
    stopPolling() {
      clearInterval(this.pollTimer)
      this.pollTimer = null
    },
  },
}
</script>

<style scoped>
.ke-shell{--ke-accent:var(--accent-color);--ke-accent-dark:var(--accent-color);--ke-accent-soft:color-mix(in srgb,var(--accent-color) 12%,transparent);--ke-ink:var(--text-primary);--ke-muted:var(--text-secondary);--ke-line:var(--border-color);--ke-bg:var(--bg-primary);position:fixed;inset:0;z-index:1000;container-type:inline-size;flex:1;min-width:0;height:100%;display:flex;flex-direction:column;background:var(--ke-bg);color:var(--ke-ink);font-family:inherit}.ke-shell button,.ke-shell input,.ke-shell select,.ke-shell textarea{font:inherit}.ke-shell svg{width:16px;height:16px;flex:none}.ke-topbar{height:64px;flex:none;background:var(--bg-secondary);border-bottom:1px solid var(--ke-line);display:flex;align-items:center;justify-content:space-between;padding:0 20px;z-index:5}.ke-topbar-title,.ke-topbar-actions,.ke-inline-actions,.ke-title-row,.ke-base-meta,.ke-tool-row{display:flex;align-items:center}.ke-topbar-title{gap:10px}.ke-topbar-title h1{font-size:17px;line-height:1.2;margin:0;font-weight:650}.ke-topbar-title span{display:block;margin-top:3px;color:var(--text-secondary);font-size:11px}.ke-topbar-actions{gap:8px}.ke-button{height:34px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:9px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:6px;color:var(--text-secondary);font-size:12px;cursor:pointer;transition:.18s ease;box-sizing:border-box}.ke-button:hover{background:var(--bg-tertiary);border-color:var(--border-color)}.ke-button.primary{background:var(--ke-accent);border-color:var(--ke-accent);color:#fff;box-shadow:0 3px 10px color-mix(in srgb,var(--ke-accent) 22%,transparent)}.ke-button.primary:hover{background:var(--ke-accent-dark);border-color:var(--ke-accent-dark);transform:translateY(-1px)}.ke-button.quiet{background:var(--bg-secondary)}.ke-button.danger{background:#d94b4b;border-color:#d94b4b;color:#fff}.ke-button.icon-only{width:34px;padding:0}.ke-button:disabled,.ke-icon-button:disabled{opacity:.45;cursor:not-allowed;transform:none}.ke-icon-button{width:32px;height:32px;border:1px solid var(--border-color);background:var(--bg-secondary);border-radius:9px;display:grid;place-items:center;color:var(--text-secondary);cursor:pointer;transition:.18s}.ke-icon-button:hover{background:var(--bg-tertiary);color:var(--text-primary)}.ke-icon-button.subtle{border-color:transparent;background:transparent}.danger-hover:hover,.danger-text{color:#cc4545!important;background:#fff3f2!important;border-color:#f3cdca!important}.ke-health{display:inline-flex;align-items:center;gap:6px;color:var(--text-secondary);font-size:11px;margin-right:4px}.ke-health i,.ke-status i{width:7px;height:7px;border-radius:50%;background:#e2a22e}.ke-health.ready i,.ke-status.ready i,.ke-status.succeeded i{background:var(--ke-accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--ke-accent) 12%,transparent)}
.ke-workspace{position:relative;flex:1;min-height:0;display:grid;grid-template-columns:var(--ke-library-width,252px) 6px minmax(490px,1fr) 6px var(--ke-ask-width,344px);gap:0}.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,252px) 6px minmax(540px,1fr)}.ke-library-panel,.ke-content-panel,.ke-ask-panel{min-height:0;background:#fff}.ke-library-panel{border-right:1px solid var(--ke-line);display:flex;flex-direction:column;padding:18px 12px 12px}.ke-resizer{position:relative;width:6px;min-width:6px;cursor:col-resize;touch-action:none;z-index:25;outline:none;background:var(--bg-secondary)}.ke-resizer:after{content:"";position:absolute;inset:0 2px;background:transparent;transition:.15s}.ke-resizer:hover:after,.ke-resizer:focus-visible:after{background:var(--accent-color);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent-color) 14%,transparent)}.ke-library-heading{display:flex;align-items:center;justify-content:space-between;padding:0 4px 14px}.ke-library-heading>div{display:flex;flex-direction:column;gap:3px}.ke-library-heading strong{font-size:14px}.ke-library-heading small{font-size:10px;color:#9aa09c}.ke-search-box{height:34px;border:1px solid #e1e5e2;background:#fafbfa;border-radius:9px;display:flex;align-items:center;gap:7px;padding:0 9px;color:#9ba29e;transition:.18s}.ke-search-box:focus-within{border-color:#9fd8c0;background:#fff;box-shadow:0 0 0 3px rgba(37,161,111,.08)}.ke-search-box input{min-width:0;flex:1;border:0;outline:0;background:transparent;color:#303632;font-size:12px}.ke-search-box button{border:0;background:transparent;color:#9aa19c;display:grid;place-items:center;padding:0;cursor:pointer}.sidebar-search{margin-bottom:14px}.ke-space-tabs{display:flex;flex-direction:column;gap:3px}.ke-space-tabs button{height:36px;border:0;background:transparent;border-radius:9px;padding:0 10px;display:grid;grid-template-columns:18px 1fr auto;gap:8px;text-align:left;align-items:center;color:#68716b;font-size:12px;cursor:pointer}.ke-space-tabs button:hover{background:#f5f7f5}.ke-space-tabs button.active{background:var(--ke-accent-soft);color:var(--ke-accent-dark);font-weight:600}.ke-space-tabs b{font-weight:500;font-size:10px;color:#9aa19d}.ke-base-list{flex:1;min-height:0;overflow:auto;padding-top:14px;margin-top:12px;border-top:1px solid #f0f2f0}.ke-base-card{width:100%;border:1px solid transparent;background:transparent;border-radius:11px;padding:8px;display:flex;gap:9px;align-items:center;text-align:left;color:inherit;cursor:pointer;transition:.18s}.ke-base-card:hover{background:#f8faf8}.ke-base-card.active{background:#f2f8f5;border-color:#d9eee4}.ke-base-cover,.ke-hero-cover{display:grid;place-items:center;background:linear-gradient(145deg,#45bd8a,#229568);color:#fff;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}.ke-base-cover{width:34px;height:40px;border-radius:5px 9px 9px 5px}.ke-base-cover.team,.ke-hero-cover.team{background:linear-gradient(145deg,#5d9ee8,#557bc8)}.ke-base-cover.shared,.ke-hero-cover.shared{background:linear-gradient(145deg,#a788e7,#795fc4)}.ke-base-copy{display:flex;flex:1;min-width:0;flex-direction:column;gap:4px}.ke-base-copy strong{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-base-copy small{font-size:10px;color:#959c97}.ke-chevron{width:13px!important;color:#bec3bf;opacity:0}.ke-base-card:hover .ke-chevron,.ke-base-card.active .ke-chevron{opacity:1}.ke-empty-small{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#a2a9a4;text-align:center;font-size:11px;padding:32px 12px}.ke-empty-small svg{width:25px;height:25px}.ke-empty-small button{border:0;background:transparent;color:var(--ke-accent-dark);font-size:11px;cursor:pointer}.ke-library-tip{display:flex;align-items:flex-start;gap:7px;background:#f8faf8;border-radius:9px;padding:10px;color:#929994;font-size:9px;line-height:1.45}.ke-library-tip svg{width:14px;height:14px;color:#72a78e}
.ke-content-panel{position:relative;display:flex;flex-direction:column;overflow:hidden;background:#fff}.ke-base-hero{flex:none;padding:24px 26px 20px;display:flex;justify-content:space-between;gap:18px;border-bottom:1px solid var(--ke-line)}.ke-base-identity{display:flex;gap:15px;min-width:0}.ke-hero-cover{width:48px;height:58px;border-radius:7px 12px 12px 7px}.ke-hero-cover svg{width:22px;height:22px}.ke-title-row{gap:9px}.ke-title-row h2{margin:0;font-size:20px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-role{padding:3px 7px;background:#f1f4f2;border-radius:5px;color:#768078;font-size:9px}.ke-base-identity p{max-width:620px;margin:6px 0 10px;color:#868e89;font-size:11px;line-height:1.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-base-meta{gap:14px;color:#9aa09c;font-size:9px}.ke-base-meta span{display:flex;align-items:center;gap:4px}.ke-base-meta svg{width:12px;height:12px}.ke-inline-actions{gap:6px;align-self:flex-start}.ke-content-toolbar{flex:none;padding:13px 20px 12px;border-bottom:1px solid var(--ke-line)}.ke-folder-tabs{display:flex;align-items:center;gap:4px;overflow-x:auto;padding-bottom:11px}.ke-folder-tabs button{height:28px;border:0;background:transparent;border-radius:7px;padding:0 8px;display:flex;align-items:center;gap:5px;color:#777f7a;font-size:10px;white-space:nowrap;cursor:pointer}.ke-folder-tabs button:hover{background:#f5f7f5}.ke-folder-tabs button.active{background:#edf7f2;color:var(--ke-accent-dark);font-weight:600}.ke-folder-tabs button span{color:#9ca39e;font-size:9px}.ke-folder-tabs svg{width:12px;height:12px}.ke-folder-tabs .folder-add{color:var(--ke-accent-dark);margin-left:3px}.ke-folder-tabs .folder-manage{width:27px;padding:0;justify-content:center}.ke-tool-row{gap:7px}.ke-tool-row>.ke-search-box{flex:1;max-width:300px}.ke-select-wrap{height:34px;border:1px solid #e1e5e2;background:#fff;border-radius:9px;padding:0 8px;display:flex;align-items:center;color:#929a94}.ke-select-wrap select,.ke-folder-select select{border:0;outline:0;background:transparent;color:#636b66;font-size:10px}.ke-view-switch{height:34px;background:#f3f5f3;border-radius:9px;padding:3px;display:flex}.ke-view-switch button{width:28px;border:0;background:transparent;color:#9aa19c;border-radius:7px;display:grid;place-items:center;cursor:pointer}.ke-view-switch button.active{background:#fff;color:#3d4640;box-shadow:0 1px 4px rgba(30,45,35,.1)}.ke-upload-queue{flex:none;margin:12px 20px 0;border:1px solid #e3e8e4;border-radius:11px;background:#fbfcfb;padding:11px}.ke-upload-queue header{display:flex;align-items:center;gap:7px;margin-bottom:8px;font-size:10px}.ke-upload-queue header span{color:#9ba19d}.ke-upload-item{display:grid;grid-template-columns:28px 1fr 72px;gap:8px;align-items:center;padding:6px 0}.ke-mini-file{width:26px;height:28px;border-radius:6px;background:#edf7f2;color:var(--ke-accent);display:grid;place-items:center}.ke-upload-item>div{min-width:0}.ke-upload-item strong{display:block;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px}.ke-progress{height:4px;background:#e8ece9;border-radius:8px;overflow:hidden}.ke-progress i{display:block;height:100%;background:var(--ke-accent);transition:width .3s}.ke-upload-item>b{font-size:9px;font-weight:500;color:#7f8782}.ke-upload-item>b.failed{color:#d54e4e}
.ke-documents{flex:1;min-height:0;overflow:auto;padding:18px 20px 28px}.ke-documents.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));align-content:start;gap:12px}.ke-document-card{position:relative;border:1px solid #e6eae7;border-radius:12px;background:#fff;padding:14px;min-width:0;transition:.18s}.grid .ke-document-card{min-height:150px;display:flex;flex-direction:column}.ke-document-card:hover{border-color:#ccd8d1;box-shadow:0 8px 24px rgba(39,64,48,.07);transform:translateY(-1px)}.ke-doc-open{width:100%;border:0;background:transparent;padding:0;display:grid;grid-template-columns:39px minmax(0,1fr);align-items:start;column-gap:10px;text-align:left;min-width:0;color:inherit;cursor:pointer}.ke-doc-open:disabled{cursor:default}.ke-file-icon{width:39px;min-width:39px;height:43px;box-sizing:border-box;display:grid;place-items:center;border-radius:8px;background:#f7f9f7;overflow:hidden}.ke-document-copy{width:100%;min-width:0;overflow:hidden;display:flex;flex-direction:column;gap:5px;padding-top:2px}.ke-document-copy strong,.ke-document-copy small{display:block;max-width:100%;min-width:0;overflow:hidden;text-overflow:ellipsis}.ke-document-copy strong{font-size:11px;line-height:1.45;white-space:nowrap}.grid .ke-document-copy strong{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}.ke-document-copy small{color:#9aa19c;font-size:9px;white-space:nowrap}.ke-document-details{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:15px}.grid .ke-document-details{margin-top:auto;padding-top:15px}.ke-status{display:inline-flex;align-items:center;gap:5px;color:#6e7771;font-size:9px;white-space:nowrap}.ke-status i{width:6px;height:6px}.ke-status em{font-style:normal;color:#9aa19c}.ke-status.processing i,.ke-status.running i{background:#4b91e4;animation:ke-pulse 1.2s infinite}.ke-status.failed i,.ke-status.partial_failed i{background:#e25555}.ke-folder-select{min-width:0;display:flex;align-items:center;gap:3px;color:#9aa19c}.ke-folder-select svg{width:11px;height:11px}.ke-folder-select select{max-width:80px;text-overflow:ellipsis}.ke-error-text{font-size:9px;color:#cc4b4b;line-height:1.4;margin:7px 0 0}.ke-row-actions{position:absolute;right:9px;top:9px;display:flex;align-items:center;gap:2px;background:rgba(255,255,255,.96);border-radius:8px;padding:2px;opacity:0;transform:translateY(-3px);transition:.16s;box-shadow:0 2px 10px rgba(30,45,35,.08)}.ke-document-card:hover .ke-row-actions,.ke-row-actions:focus-within{opacity:1;transform:none}.ke-row-actions button{height:26px;border:0;background:transparent;border-radius:6px;padding:0 6px;display:flex;align-items:center;gap:3px;color:#69726c;font-size:9px;cursor:pointer}.ke-row-actions button:hover{background:#f2f5f3;color:#26312a}.ke-row-actions svg{width:12px;height:12px}.list.ke-documents{padding-top:8px}.list .ke-document-card{border-width:0 0 1px;border-radius:0;padding:12px 8px;display:grid;grid-template-columns:minmax(220px,1fr) 240px 145px;align-items:center;gap:10px}.list .ke-document-card:hover{transform:none;box-shadow:none;background:#fafbfa}.list .ke-document-details{margin:0}.list .ke-row-actions{position:static;opacity:1;transform:none;box-shadow:none;justify-content:flex-end}.list .ke-row-actions button span{display:none}.list .ke-error-text{grid-column:1/-1}.ke-document-skeletons{grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}.ke-document-skeletons span{height:150px;border-radius:12px;background:linear-gradient(90deg,#f4f6f4 25%,#fafbfa 37%,#f4f6f4 63%);background-size:400% 100%;animation:ke-shimmer 1.4s infinite}.ke-empty-documents{grid-column:1/-1;align-self:center;margin:auto;display:flex;flex-direction:column;align-items:center;text-align:center;padding:45px 20px}.ke-empty-documents>div,.ke-empty-illustration{width:60px;height:60px;border-radius:18px;background:var(--ke-accent-soft);color:var(--ke-accent);display:grid;place-items:center}.ke-empty-documents>div svg,.ke-empty-illustration svg{width:27px;height:27px}.ke-empty-documents h3{margin:14px 0 5px;font-size:14px}.ke-empty-documents p{margin:0 0 15px;color:#929994;font-size:11px}.ke-drop-mask{position:absolute;inset:10px;z-index:15;border:2px dashed #71c39f;border-radius:16px;background:rgba(237,250,244,.94);display:grid;place-items:center;pointer-events:none}.ke-drop-mask>div{display:flex;flex-direction:column;align-items:center;gap:7px;color:var(--ke-accent-dark)}.ke-drop-mask svg{width:36px;height:36px}.ke-drop-mask strong{font-size:15px}.ke-drop-mask span{color:#7b8c82;font-size:11px}
.ke-ask-panel{border-left:1px solid var(--ke-line);display:flex;flex-direction:column;overflow:hidden;background:#fcfdfc}.ke-ask-header{height:67px;flex:none;padding:0 14px;display:flex;align-items:center;gap:9px;border-bottom:1px solid var(--ke-line);background:#fff}.ke-ai-avatar{width:31px;height:31px;border-radius:10px;background:linear-gradient(145deg,#32b981,#21865f);color:#fff;display:grid;place-items:center;box-shadow:0 4px 12px rgba(37,161,111,.2)}.ke-ai-avatar.small{width:23px;height:23px;border-radius:7px}.ke-ai-avatar.small svg{width:12px;height:12px}.ke-ask-header>div:nth-child(2){display:flex;flex:1;min-width:0;flex-direction:column;gap:3px}.ke-ask-header strong{font-size:12px}.ke-ask-header small{font-size:9px;color:#969d98;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-answer{flex:1;min-height:0;overflow:auto;padding:18px}.ke-answer-welcome{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}.ke-answer-orb{width:52px;height:52px;border-radius:18px;background:radial-gradient(circle at 30% 25%,#fff 0,#e4f7ee 35%,#cceede 100%);color:var(--ke-accent);display:grid;place-items:center;box-shadow:0 8px 25px rgba(38,126,88,.12)}.ke-answer-orb svg{width:24px;height:24px}.ke-answer-welcome h3{font-size:14px;margin:15px 0 6px}.ke-answer-welcome>p{max-width:235px;margin:0;color:#8d9690;font-size:10px;line-height:1.7}.ke-question-suggestions{width:100%;margin-top:19px;display:flex;flex-direction:column;gap:7px}.ke-question-suggestions button{border:1px solid #e3e8e4;background:#fff;border-radius:10px;padding:10px 11px;display:flex;justify-content:space-between;align-items:center;color:#626b65;text-align:left;font-size:10px;cursor:pointer;transition:.16s}.ke-question-suggestions button:hover{border-color:#b9ddcb;background:#f5fbf8;color:var(--ke-accent-dark)}.ke-question-suggestions svg{width:12px;height:12px}.ke-answer-loading{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#8b948e;font-size:10px}.ke-question-bubble{margin-left:28px;padding:9px 11px;border-radius:11px 3px 11px 11px;background:#eef7f3;color:#3f4d45;font-size:11px;line-height:1.55}.ke-answer-label{display:flex;align-items:center;gap:7px;margin:18px 0 10px;font-size:10px}.ke-answer-text{font-size:11px;line-height:1.85;color:#3e4741;white-space:pre-wrap}.ke-evidence-title{display:flex;align-items:center;gap:5px;margin:18px 0 7px;color:#737d76;font-size:10px;font-weight:600}.ke-evidence-title svg{width:13px;height:13px}.ke-evidence{width:100%;margin-top:7px;padding:10px;border:1px solid #e3e8e4;border-radius:10px;background:#fff;text-align:left;color:inherit;cursor:pointer}.ke-evidence:hover{border-color:#c6ddd1;background:#fbfdfc}.ke-evidence>div{display:flex;gap:7px;align-items:center;font-size:9px}.ke-evidence b{width:18px;height:18px;border-radius:5px;background:var(--ke-accent-soft);color:var(--ke-accent-dark);display:grid;place-items:center}.ke-evidence strong{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-evidence span{margin-left:auto;color:#9aa19c}.ke-evidence p{font-size:9px;line-height:1.55;color:#747d77;margin:7px 0 0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.ke-warning{display:flex;gap:6px;color:#a56a14;font-size:9px;line-height:1.5}.ke-warning svg{width:13px;height:13px}.ke-ask-form{flex:none;padding:12px 14px 13px}.ke-composer{border:1px solid #dce3de;background:#fff;border-radius:13px;padding:8px 9px;box-shadow:0 5px 18px rgba(42,62,49,.06);transition:.18s}.ke-composer:focus-within{border-color:#91ceb2;box-shadow:0 0 0 3px rgba(37,161,111,.08)}.ke-composer.disabled{background:#f7f8f7}.ke-composer textarea{width:100%;min-height:50px;box-sizing:border-box;border:0;outline:0;resize:none;background:transparent;color:#303733;font-size:11px;line-height:1.55}.ke-composer>div{display:flex;align-items:center;justify-content:space-between}.ke-composer>div span{display:flex;align-items:center;gap:4px;color:#9aa19c;font-size:8px}.ke-composer>div span svg{width:11px;height:11px}.ke-composer>div button{width:28px;height:28px;border:0;border-radius:9px;background:var(--ke-accent);color:#fff;display:grid;place-items:center;cursor:pointer}.ke-composer>div button:disabled{background:#d8ddda;cursor:not-allowed}.ke-ask-form>small{display:block;margin-top:6px;text-align:center;color:#a1a7a3;font-size:8px}.ke-ask-fab{position:absolute;right:20px;bottom:20px;height:40px;border:0;border-radius:20px;background:var(--ke-accent);color:#fff;padding:0 15px;display:flex;align-items:center;gap:7px;box-shadow:0 8px 24px rgba(37,161,111,.25);cursor:pointer;z-index:10}.ke-ask-fab:disabled{display:none}.ke-ask-fab span{font-size:11px}
.ke-loading-page,.ke-empty-page{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#858e88}.ke-loading-page p{font-size:11px}.ke-empty-page h2{font-size:17px;color:#303632;margin:15px 0 6px}.ke-empty-page p{font-size:11px;margin:0 0 16px}.ke-empty-page.compact{height:100%}.ke-spinner{width:22px;height:22px;border:2px solid #dce9e2;border-top-color:var(--ke-accent);border-radius:50%;animation:ke-spin .75s linear infinite}
.ke-modal-mask,.ke-drawer-mask{position:fixed;inset:0;background:rgba(26,31,28,.38);z-index:1200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(2px)}.ke-modal{width:min(430px,calc(100vw - 32px));max-height:90vh;overflow:auto;background:#fff;border-radius:16px;padding:21px;box-shadow:0 24px 65px rgba(23,35,27,.2)}.ke-modal.small{width:min(360px,calc(100vw - 32px))}.ke-modal.wide{width:min(600px,calc(100vw - 32px))}.ke-modal header,.ke-preview-modal header,.ke-drawer header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.ke-modal header h3,.ke-preview-modal h3,.ke-drawer h2{margin:0;font-size:16px}.ke-modal header p,.ke-drawer header p{margin:4px 0 0;color:#929a95;font-size:10px;line-height:1.5}.ke-modal header>button,.ke-preview-modal header>button,.ke-drawer header>button{width:30px;height:30px;border:0;background:transparent;border-radius:8px;display:grid;place-items:center;color:#7d8580;cursor:pointer}.ke-modal header>button:hover,.ke-preview-modal header>button:hover,.ke-drawer header>button:hover{background:#f3f5f3}.ke-modal>label{display:flex;flex-direction:column;gap:6px;margin-top:16px;color:#59615c;font-size:11px}.ke-modal input,.ke-modal select,.ke-modal textarea{width:100%;box-sizing:border-box;border:1px solid #dfe4e0;border-radius:9px;background:#fff;padding:9px 10px;color:#303632;outline:0;font-size:11px}.ke-modal input:focus,.ke-modal select:focus,.ke-modal textarea:focus{border-color:#91ceb2;box-shadow:0 0 0 3px rgba(37,161,111,.07)}.ke-modal textarea{min-height:82px;resize:vertical}.ke-modal label>small{color:#9ba19d;font-size:9px;line-height:1.5}.ke-modal footer{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.ke-subject-search{display:grid;grid-template-columns:90px 1fr;gap:8px;margin-top:17px}.ke-subject-search>select{border:1px solid #dfe4e0;border-radius:9px;background:#fff;padding:0 8px;font-size:11px}.ke-subject-results{border:1px solid #e3e7e4;border-radius:10px;margin-top:7px;max-height:150px;overflow:auto}.ke-subject-results button{width:100%;border:0;border-bottom:1px solid #eff1ef;background:#fff;padding:8px 10px;display:grid;grid-template-columns:30px 1fr auto;gap:8px;align-items:center;text-align:left;color:inherit;cursor:pointer}.ke-subject-results button:hover{background:#f6faf8}.ke-subject-results button>span:nth-child(2){display:flex;flex-direction:column;gap:2px}.ke-subject-results strong{font-size:10px}.ke-subject-results small{color:#969e98;font-size:9px}.ke-subject-avatar{width:28px;height:28px;border-radius:9px;background:#eef7f3;color:var(--ke-accent-dark);display:grid;place-items:center}.ke-subject-avatar svg{width:14px;height:14px}.ke-permission-heading{display:flex;gap:6px;align-items:center;margin-top:18px;padding-bottom:7px;border-bottom:1px solid #edf0ed;font-size:11px}.ke-permission-heading span{color:#9aa19c}.ke-permission-list{max-height:250px;overflow:auto}.ke-permission-list>div{display:grid;grid-template-columns:32px 1fr 105px 32px;gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid #f0f2f0}.ke-permission-copy{display:flex;flex-direction:column;gap:2px}.ke-permission-copy strong{font-size:10px}.ke-permission-copy small{font-size:9px;color:#9aa19c}.ke-permission-list>div>select{padding:7px}.ke-permission-list>p{color:#9aa19c;text-align:center;font-size:10px;padding:20px}.ke-modal.confirm{text-align:center;width:min(360px,calc(100vw - 32px));overflow:visible}.ke-confirm-icon{width:45px;height:45px;margin:0 auto 12px;border-radius:14px;background:#fff1ef;color:#d34c4c;display:grid;place-items:center}.ke-confirm-icon svg{width:22px;height:22px}.ke-modal.confirm h3{font-size:16px;margin:0}.ke-modal.confirm>p{font-size:10px;color:#838b86;line-height:1.65;margin:8px 10px}.ke-modal.confirm footer{justify-content:center}.preview-mask{padding:24px}.ke-preview-modal{width:min(1080px,94vw);height:min(820px,90vh);background:#fff;border-radius:15px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 24px 70px rgba(18,29,22,.24)}.ke-preview-modal header{height:55px;flex:none;align-items:center;padding:0 16px;border-bottom:1px solid var(--ke-line)}.ke-preview-modal header>div{display:flex;align-items:center;gap:8px;min-width:0}.ke-preview-modal h3{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-preview-modal iframe,.ke-preview-modal img,.ke-preview-modal pre{flex:1;min-height:0;width:100%;border:0;object-fit:contain;background:#f5f7f5;overflow:auto;box-sizing:border-box;padding:16px}.ke-drawer-mask{justify-content:flex-end;align-items:stretch}.ke-drawer{width:min(400px,90vw);background:#fff;padding:20px;overflow:auto;box-shadow:-18px 0 50px rgba(28,38,31,.13)}.ke-drawer .refresh{margin:15px 0;width:100%}.ke-operation{padding:12px 0;border-top:1px solid #edf0ed}.ke-operation>div:first-child{display:grid;grid-template-columns:30px 1fr auto;gap:8px;align-items:center;margin-bottom:9px}.ke-operation-icon{width:28px;height:28px;background:#f2f6f3;color:#6f7c74;border-radius:8px;display:grid;place-items:center}.ke-operation>div:first-child>div{display:flex;flex-direction:column;gap:2px}.ke-operation strong{font-size:10px}.ke-operation small{color:#949c97;font-size:8px}.ke-operation p{color:#c94a4a;font-size:9px}.ke-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#26342c;color:#fff;border-radius:10px;padding:10px 14px;z-index:1600;display:flex;align-items:center;gap:7px;font-size:11px;box-shadow:0 9px 28px rgba(22,36,27,.24)}.ke-toast.error{background:#b83f43}.ke-toast svg{width:14px;height:14px}.ke-toast-enter-active,.ke-toast-leave-active{transition:.2s}.ke-toast-enter-from,.ke-toast-leave-to{opacity:0;transform:translate(-50%,8px)}
.ke-workspace{grid-template-columns:var(--ke-library-width,252px) 6px minmax(490px,1fr) 6px var(--ke-ask-width,344px)}.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,252px) 6px minmax(540px,1fr)}
.sidebar-search{margin-bottom:12px}.ke-space-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:4px}.ke-space-group{border-radius:11px;overflow:hidden}.ke-space-group.active{background:#f8faf8}.ke-space-trigger{width:100%;min-height:48px;border:0;background:transparent;border-radius:10px;padding:7px 8px;display:grid;grid-template-columns:19px minmax(0,1fr) auto 14px;gap:8px;align-items:center;text-align:left;color:#5f6862;cursor:pointer}.ke-space-trigger:hover{background:#f4f7f5}.ke-space-group.active>.ke-space-trigger{color:var(--ke-accent-dark);background:#eef7f3}.ke-space-trigger>span{display:flex;min-width:0;flex-direction:column;gap:2px}.ke-space-trigger strong{font-size:11px}.ke-space-trigger small{font-size:8px;color:#999f9b;font-weight:400}.ke-space-trigger b{font-size:9px;font-weight:500;color:#979f99}.ke-space-trigger>svg:last-child{width:12px;height:12px;transition:transform .18s}.ke-space-trigger>svg.rotated{transform:rotate(180deg)}.ke-space-bases{margin:5px 0 8px 17px;padding-left:9px;border-left:2px solid #d9e9e0;display:flex;flex-direction:column;gap:2px}.ke-space-bases .ke-base-card{border-radius:9px;padding:7px}.ke-space-bases .ke-base-card.active{background:#fff;box-shadow:0 2px 8px rgba(31,68,47,.05)}.ke-space-bases .ke-base-cover{width:30px;height:35px}.ke-space-empty{padding:10px 6px;color:#9ba29d;font-size:9px;display:flex;align-items:center;justify-content:space-between;gap:6px}.ke-space-empty>small{display:block;max-width:105px;text-align:right;color:#9ba29d;line-height:1.35}.ke-space-empty button{border:0;background:transparent;color:var(--ke-accent-dark);display:flex;align-items:center;gap:3px;cursor:pointer}
.ke-folder-breadcrumbs{min-height:30px;display:flex;align-items:center;gap:3px;padding-bottom:11px;overflow-x:auto}.ke-folder-breadcrumbs>svg,.ke-folder-crumb>svg{width:12px;height:12px;color:#b3b8b5}.ke-folder-breadcrumbs>button,.ke-folder-crumb>button{height:28px;border:0;background:transparent;border-radius:7px;padding:0 7px;color:#8b928e;font-size:10px;white-space:nowrap;cursor:pointer}.ke-folder-breadcrumbs>button:hover,.ke-folder-breadcrumbs>button.current,.ke-folder-crumb>button:hover,.ke-folder-crumb>button.current{background:#f1f6f3;color:#27312b;font-weight:600}.ke-folder-crumb{display:inline-flex;align-items:center;gap:3px}.ke-folder-breadcrumbs .ke-folder-back{width:28px;padding:0;display:grid;place-items:center}.ke-folder-actions{display:flex;align-items:center;gap:3px;margin-left:auto;padding-left:12px}.ke-folder-actions button{height:28px;border:0;background:#f5f7f5;border-radius:7px;padding:0 7px;display:flex;align-items:center;gap:4px;color:#6e7771;font-size:9px;white-space:nowrap;cursor:pointer}.ke-folder-actions button:first-child{color:var(--ke-accent-dark);background:#edf7f2}.ke-folder-actions svg{width:12px;height:12px}
.ke-folder-grid{grid-column:1/-1;display:flex;flex-direction:column;gap:2px;margin-bottom:11px}.ke-folder-card{width:100%;height:62px;border:0;border-bottom:1px solid #f0f2f0;background:#fff;padding:5px 10px;display:grid;grid-template-columns:54px minmax(0,1fr) 18px;gap:10px;align-items:center;text-align:left;color:inherit;border-radius:8px;cursor:pointer}.ke-folder-card:hover{background:#f8faf8}.ke-folder-art{position:relative;width:49px;height:35px;border-radius:4px 5px 5px 5px;background:linear-gradient(180deg,#bce7d1,#a8dbc0);color:#fff;display:grid;place-items:center}.ke-folder-art:before{content:"";position:absolute;left:3px;top:-6px;width:22px;height:8px;border-radius:4px 5px 0 0;background:#9bd5b8}.ke-folder-art svg{position:relative;width:23px;height:23px}.ke-folder-card>span:nth-child(2){display:flex;min-width:0;flex-direction:column;gap:4px}.ke-folder-card strong{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ke-folder-card small{font-size:9px;color:#acb1ae}.ke-folder-card>svg{color:#c1c6c2;width:14px;height:14px}
.ke-status.processing i,.ke-status.running i{animation:none}.ke-processing-age{margin-left:auto;color:#9aa19c;font-size:9px;white-space:nowrap}.ke-preview-modal>div:not(header){flex:1;min-height:0;overflow:auto}.ke-preview-modal ::v-deep .docx-wrapper{min-height:100%;background:#eef1ef}.ke-preview-modal ::v-deep .vue-office-excel{height:100%}.ke-preview-modal ::v-deep .pptx-preview-wrapper{height:100%;overflow:auto}
@keyframes ke-pulse{50%{opacity:.35}}@keyframes ke-spin{to{transform:rotate(360deg)}}@keyframes ke-shimmer{0%{background-position:100% 0}100%{background-position:0 0}}
@container(max-width:1100px){.ke-workspace,.ke-workspace.ask-closed{grid-template-columns:var(--ke-library-width,220px) 6px minmax(0,1fr)}.ke-resizer.ask{display:none}.ke-workspace:not(.ask-closed) .ke-content-panel{margin-right:min(var(--ke-ask-width,360px),88cqw)}.ke-ask-panel{position:absolute;right:0;top:0;bottom:0;width:min(var(--ke-ask-width,360px),88cqw);z-index:20;box-shadow:-14px 0 35px rgba(28,43,33,.13)}.ke-base-meta span:last-child{display:none}.ke-tool-row{flex-wrap:wrap}.ke-tool-row>.ke-search-box{max-width:none}.list .ke-document-card{grid-template-columns:minmax(180px,1fr) 190px 130px}}
@container(max-width:700px){.ke-topbar{padding:0 10px}.ke-topbar-title span,.ke-topbar-actions .quiet{display:none}.ke-workspace,.ke-workspace.ask-closed{display:block}.ke-library-panel,.ke-resizer{display:none}.ke-content-panel{height:100%}.ke-base-hero{padding:18px 15px}.ke-inline-actions{position:absolute;right:14px}.ke-base-identity{padding-right:80px}.ke-base-meta{flex-wrap:wrap}.ke-content-toolbar{padding:11px 12px}.ke-documents{padding:12px}.ke-documents.grid{grid-template-columns:1fr}.ke-view-switch{display:none}.list .ke-document-card{display:block}.list .ke-document-details{margin-top:12px}.list .ke-row-actions{margin-top:8px;justify-content:flex-start}.ke-base-identity p{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}

/* EasyAgent surface and dark-mode alignment. */
.ke-library-panel,.ke-content-panel,.ke-ask-panel,.ke-ask-header,.ke-document-card,.ke-folder-card,.ke-modal,.ke-preview-modal,.ke-drawer,.ke-composer,.ke-evidence,.ke-question-suggestions button{background:var(--bg-secondary);color:var(--text-primary)}
.ke-search-box,.ke-select-wrap,.ke-upload-queue,.ke-view-switch,.ke-role,.ke-operation-icon,.ke-file-icon{background:var(--bg-tertiary);border-color:var(--border-color);color:var(--text-secondary)}
.ke-search-box input,.ke-select-wrap select,.ke-folder-select select,.ke-modal input,.ke-modal select,.ke-modal textarea,.ke-composer textarea{color:var(--text-primary);background:transparent}
.ke-search-box:focus-within,.ke-modal input:focus,.ke-modal select:focus,.ke-modal textarea:focus,.ke-composer:focus-within{border-color:var(--accent-color);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent-color) 14%,transparent)}
.ke-space-group.active,.ke-space-trigger:hover,.ke-base-card:hover,.ke-folder-card:hover,.list .ke-document-card:hover,.ke-row-actions button:hover{background:var(--bg-tertiary)}
.ke-space-group.active>.ke-space-trigger,.ke-base-card.active,.ke-folder-actions button:first-child,.ke-question-bubble{background:color-mix(in srgb,var(--accent-color) 11%,transparent);color:var(--accent-color)}
.ke-space-bases .ke-base-card.active,.ke-view-switch button.active,.ke-row-actions{background:var(--bg-secondary)}
.ke-space-bases{border-left-color:color-mix(in srgb,var(--accent-color) 35%,var(--border-color))}
.ke-modal input,.ke-modal select,.ke-modal textarea,.ke-subject-search>select,.ke-subject-results,.ke-permission-heading,.ke-permission-list>div,.ke-operation{border-color:var(--border-color)}
.ke-subject-results button{background:var(--bg-secondary);color:var(--text-primary);border-color:var(--border-color)}
.ke-subject-results button:hover{background:var(--bg-tertiary)}
.ke-preview-modal iframe,.ke-preview-modal img,.ke-preview-modal pre{background:var(--bg-primary)}
.ke-ai-avatar{background:linear-gradient(145deg,#38bdf8,#0284c7);box-shadow:0 4px 12px color-mix(in srgb,var(--accent-color) 24%,transparent)}
.ke-folder-art{background:linear-gradient(180deg,#7dd3fc,#38bdf8)}.ke-folder-art:before{background:#0ea5e9}
.ke-preview-loading{flex:1;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;color:var(--text-secondary);font-size:11px}.ke-preview-loading p{margin:0}.ke-preview-loading .ke-spinner{flex:none}
.ke-stop-button{color:#d14b4b!important}.ke-answer-text{min-height:18px}.ke-stream-cursor{display:inline-block;width:2px;height:1em;margin-left:3px;vertical-align:-2px;background:var(--accent-color);animation:ke-cursor-blink .8s steps(2,end) infinite}.ke-ask-error{margin-top:14px;padding:11px;border:1px solid color-mix(in srgb,#d14b4b 28%,var(--border-color));border-radius:10px;background:color-mix(in srgb,#d14b4b 7%,var(--bg-secondary));color:var(--text-secondary);font-size:10px;line-height:1.55;display:grid;grid-template-columns:16px minmax(0,1fr) auto;gap:7px;align-items:start}.ke-ask-error>svg{width:15px;height:15px;color:#d14b4b;margin-top:1px}.ke-ask-error p{margin:0}.ke-ask-error button{border:0;background:transparent;color:var(--accent-color);font-weight:600;display:flex;align-items:center;gap:4px;cursor:pointer}.ke-ask-error button svg{width:12px;height:12px}@keyframes ke-cursor-blink{50%{opacity:0}}
.ke-agent-message{width:100%;min-width:0}.ke-agent-message ::v-deep .message.assistant{width:100%;max-width:none;margin:0;padding:0}.ke-agent-message ::v-deep .message-content{min-width:0}.ke-agent-message ::v-deep .process-wrapper,.ke-agent-message ::v-deep .knowledge-citations{box-sizing:border-box}.ke-permission-note{margin:10px 0 0;color:var(--text-secondary);font-size:9px;line-height:1.5}

<style>
/* 布局拖拽期间禁用文本选择（原本使用 Vue3 的 :global()，Vue2 放到非 scoped 块） */
body.ke-layout-resizing .ke-resizer:after{background:var(--accent-color);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent-color) 14%,transparent)}
</style>
