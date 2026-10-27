import { setReactive } from '../../../utils/reactiveSet.js'
import { API_BASE_URL } from '../../../config.js'
import { sendMessage, resumeStream } from '../../../api/chat.js'
import { deleteFile } from '../../../api/files.js'
import { authFetch } from '../../../api/auth.js'
import { createStreamChunkHandler as createChunkHandler } from '../services/chunkHandler.js'
import { applyApprovalRequests, buildResumeDecisions, markApprovalDecision } from '../../../utils/hitlApproval.js'

/**
 * AI 侧边栏对话引擎的流式操作部分：发送、HITL 审批恢复、重试、停止、删除附件。
 * 依赖 chatSession.js 提供的会话状态与方法（runInSession / syncSessionFromServer 等），
 * 两者需一起混入。
 */
export default {
  methods: {
    createStreamChunkHandler(ctx) {
      return createChunkHandler(this, ctx)
    },
    async handleSendMessage(message, files = [], signal, enableDeepThink = true, enableWebSearch = false, skills = []) {
      const userMsgId = `user-${Date.now()}`
      const preStreamUsage = { ...this.sessionUsage }
      // 记录本次请求开始前已累计的耗时和迭代次数，用于流式过程中实时累加
      const preStreamDuration = this.sessionDuration
      const preStreamIterationCount = this.iterationCount
      // 本次流所属的会话 ID（新会话在 start 事件后才有值）。
      // 用于用户中途切换会话时，把用量更新写入所属会话的缓存而非当前显示
      let streamSessionId = this.currentSessionId

      const contentWithFiles = message.trim().replace(/\s+/g, ' ')
      const pageContext = this.getPageContextForSend() || null

      const userMessage = {
        id: userMsgId,
        role: 'user',
        content: contentWithFiles,
        page_context: pageContext,
        files: files.map(f => ({
          filename: f.filename,
          size: f.size,
          type: f.file.type,
          file_path: f.file_path || null
        })),
        created_at: new Date().toISOString()
      }
      // 结构化技能目录名（与后端落库字段同形，见 App.vue 的同类实现）
      if (skills.length > 0) userMessage.skills = [...skills]

      this.messages.push(userMessage)

      // 立即创建 assistant 占位消息，显示等待动画
      const assistantPlaceholderId = `assistant-${Date.now()}`
      // 专家模式：随消息记住本次回答使用的专家（执行过程头部展示头像+名称）。
      // 仅存在于本次会话的内存消息中；历史会话从后端加载的消息不含该字段，
      // 展示时按无专家兜底（后端消息结构不含专家信息，持久化需另行加字段）。
      const usedExpert = (this.selectedExpert && this.selectedExpert.export_agent_id)
        ? { id: this.selectedExpert.export_agent_id, name: this.selectedExpert.export_name_cn || '专家' }
        : null
      const assistantPlaceholder = {
        id: assistantPlaceholderId,
        role: 'assistant',
        content: '',
        created_at: null,
        thinking: '',
        tool_calls: [],
        blocks: [],
        loading: true,
        expert: usedExpert
      }
      this.messages.push(assistantPlaceholder)
      this.streamingAssistantId = assistantPlaceholderId

      let assistantMsgId = null
      let assistantMessageCreated = false

      const streamCtx = {
        isResume: false,
        // 本轮使用的专家 ID：start 事件里据此写「专家 -> 会话」映射（换专家 = 换会话）
        expertId: (this.selectedExpert && this.selectedExpert.export_agent_id) || '',
        get assistantMsgId() { return assistantMsgId },
        set assistantMsgId(v) { assistantMsgId = v },
        get streamSessionId() { return streamSessionId },
        set streamSessionId(v) { streamSessionId = v },
        preStreamUsage,
        preStreamIterationCount,
        preStreamDuration,
        initialBlockOrder: 0,
        ensureMessage: () => {
          if (!assistantMessageCreated) {
            if (assistantPlaceholderId && this.messages.find(m => m.id === assistantPlaceholderId)) {
              assistantMsgId = assistantPlaceholderId
            } else {
              assistantMsgId = `assistant-${Date.now()}`
              this.messages.push({
                id: assistantMsgId, role: 'assistant', content: '',
                created_at: null, thinking: '', tool_calls: [], blocks: [], loading: true,
                expert: usedExpert,
              })
            }
            this.streamingAssistantId = assistantMsgId
            assistantMessageCreated = true
          }
        },
        onApprovalRequired: (data) => {
          this.pendingApproval = { threadId: data.thread_id, assistantMsgId }
          // 会话列表立即亮起"待审批"标识（streamCtx 见下方 createStreamChunkHandler(streamCtx)，
          // 回调执行时已初始化完毕；普通流可能属于后台会话，优先取其 streamSessionId）
          this.syncPendingApprovalFlag(streamCtx.streamSessionId || this.currentSessionId, true)
          if (assistantMsgId && data.action_requests) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
              // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
              applyApprovalRequests(this.messages[idx].blocks, data.action_requests)
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      }
      const { onChunk } = this.createStreamChunkHandler(streamCtx)

      try {
        this.markStreaming(this.currentSessionId)
        const controller = new AbortController()
        this.currentAbortController = controller
        const abortSignal = signal || controller.signal

        // 更新会话缓存状态
        if (this.currentSessionId) {
          this.sessionStates[this.currentSessionId] = {
            ...this.sessionStates[this.currentSessionId],
            isStreaming: true,
            abortController: controller
          }
        }

        // 专家对话与通用对话只有一个入口 /agent/chat/stream：选中专家时把
        // expert_agent_id 一并上行，由后端加载该专家提示词、补装依赖技能、
        // 做职责范围预检（命中即短路拒答，Agent 不创建）。
        await sendMessage(
          this.currentSessionId,
          message,
          onChunk,
          abortSignal,
          enableDeepThink,
          files,
          this.selectedModel,
          enableWebSearch,
          skills,
          '',
          this.selectedExpert && this.selectedExpert.export_agent_id
            ? this.selectedExpert.export_agent_id
            : '',
          pageContext
        )

        await this.refreshSessionFiles(null, 500)
      } catch (e) {
        if (e.name === 'AbortError') {
          // Mark assistant message as complete (loading=false) so spinners stop
          this.runInSession(streamSessionId, streamCtx, () => {
            if (assistantMsgId) {
              const idx = this.messages.findIndex(m => m.id === assistantMsgId)
              if (idx !== -1) {
                this.messages[idx].loading = false
                // 兜底：content 为空时从已渲染的 content 块拼取（createStreamChunkHandler
                // 内部的 currentContent 局部变量在此处不可见，不能直接引用）
                if (!this.messages[idx].content && this.messages[idx].blocks && this.messages[idx].blocks.length) {
                  this.messages[idx].content = this.messages[idx].blocks
                    .filter(b => b.type === 'content')
                    .map(b => b.content || '')
                    .join('')
                }
                this.messages[idx].created_at = new Date().toISOString()
                setReactive(this.messages, idx, { ...this.messages[idx] })
              }
            }
          })
          return
        }
        console.error('发送消息失败:', e)
        this.runInSession(streamSessionId, streamCtx, () => {
          if (assistantMsgId) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].loading = false
              this.messages[idx].error = e.message || '发送消息失败，请检查网络连接'
              setReactive(this.messages, idx, { ...this.messages[idx] })
            } else {
              // 消息不存在，添加一条错误消息
              this.messages.push({
                id: assistantMsgId,
                role: 'assistant',
                content: '',
                error: e.message || '发送消息失败，请检查网络连接',
                loading: false,
                created_at: new Date().toISOString(),
                blocks: []
              })
            }
          }
        })
        // 仅当前展示会话的失败才提示到界面；后台会话失败不打扰当前会话
        if (!streamSessionId || streamSessionId === this.loadedSessionId) {
          this.error = e.message || '发送消息失败'
        }
      } finally {
        // HITL: 若有审批待处理，保持 isStreaming=true（用户需先审批）
        if (!this.pendingApproval) {
          const sid = streamSessionId
          const isDisplayed = !sid || sid === this.loadedSessionId
          // 兜底：流已结束（列表"进行中"标记即将移除），若该会话的消息仍标记 loading
          // 就强制收尾，避免"会话列表已显示完成、会话页仍显示执行中"。
          this.runInSession(sid, streamCtx, () => {
            let idx = assistantMsgId ? this.messages.findIndex(m => m.id === assistantMsgId) : -1
            if (idx === -1) {
              for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].role === 'assistant' && this.messages[i].loading) { idx = i; break }
              }
            }
            if (idx !== -1 && this.messages[idx].loading) {
              setReactive(this.messages, idx, { ...this.messages[idx], loading: false })
            }
          })
          // 只有展示中的会话才清显示级状态；后台流结束不能动当前会话的停止按钮等
          if (isDisplayed) {
            this.currentAbortController = null
            this.streamingAssistantId = null
          }
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
            if (!isDisplayed) this.sessionStates[sid].streamingAssistantId = null
          }
          if (sid) delete this.attachedStreamingSessions[sid]
          this.unmarkStreaming(sid)
          // 列表"进行中"标记此时移除（显示已完成）：以服务端历史为准回填，保证完整渲染
          await this.syncSessionFromServer(sid)

          await this.loadSessions()
        }
      }
    },
    // HITL: 用户审批后恢复执行。target.interruptId = 用户点的那个中断（DA-02）：
    // 只回答它，其余中断保持挂起，恢复流结束时后端会再次下发 approval_required。
    async handleToolApproval(decision, target) {
      if (!this.pendingApproval) return

      const currentApproval = this.pendingApproval
      const { threadId, assistantMsgId } = currentApproval
      const sessionId = this.currentSessionId

      const msgIdx = assistantMsgId ? this.messages.findIndex(m => m.id === assistantMsgId) : -1
      const pending = msgIdx !== -1 ? this.messages[msgIdx].pending_approval : null
      const actionRequests = (pending && pending.action_requests) || []
      const { decisions, interruptId } = buildResumeDecisions(actionRequests, decision, {
        interruptId: target && target.interruptId,
        // 编辑参数：只改被点的那一条（action_index 由载荷带下来）
        targetActionIndex: target && target.actionIndex,
        editedAction: target && target.editedArgs
          ? { name: target.actionName || '', args: target.editedArgs }
          : null,
      })

      // 清掉待审批按钮（恢复流期间不允许再点），只给本次作答的中断记上「已批准 / 已拒绝」
      if (msgIdx !== -1) {
        markApprovalDecision(this.messages[msgIdx].blocks, decision, actionRequests, interruptId)
        setReactive(this.messages, msgIdx, {
          ...this.messages[msgIdx],
          pending_approval: null,
          blocks: [...this.messages[msgIdx].blocks],
        })
      }
      // 本次决策已落地，重算标识（若恢复流中再遇嵌套审批会立即重新亮起）
      this.recomputePendingApprovalFlag(sessionId)

      const { onChunk } = this.createStreamChunkHandler({
        isResume: true,
        assistantMsgId,
        streamSessionId: sessionId,
        initialBlockOrder: (() => {
          const i = this.messages.findIndex(m => m.id === assistantMsgId)
          return i !== -1 ? this.messages[i].blocks.length : 0
        })(),
        onApprovalRequired: (data) => {
          // 嵌套审批
          this.pendingApproval = {
            threadId: data.thread_id,
            assistantMsgId,
          }
          // 决策后标识可能已被重算熄灭，嵌套审批到达时重新亮起
          this.syncPendingApprovalFlag(sessionId, true)
          if (assistantMsgId && data.action_requests) {
            const idx = this.messages.findIndex(m => m.id === assistantMsgId)
            if (idx !== -1) {
              this.messages[idx].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
              // 用后端权威 blocks 刷新（含本轮已完成工具的结果）
              if (Array.isArray(data.blocks) && data.blocks.length > 0) {
                this.messages[idx].blocks = data.blocks.map(b => ({ ...b }))
              }
              // 防御：嵌套审批时本轮思考已完成，把残留无 duration 的思考块置 0，
              // 避免 block.duration==null && loading=true 误显示"正在思考"
              for (const b of this.messages[idx].blocks) {
                if (b.type === 'thinking' && (b.duration == null)) {
                  b.duration = 0
                }
              }
              // 挂不上工具卡片的（子代理内部审批）由 ChatMessage 的独立审批面板展示
              applyApprovalRequests(this.messages[idx].blocks, data.action_requests)
              setReactive(this.messages, idx, { ...this.messages[idx], blocks: [...this.messages[idx].blocks] })
            }
          }
        },
      })

      try {
        const controller = new AbortController()
        this.currentAbortController = controller
        this.markStreaming(sessionId)
        this.streamingAssistantId = assistantMsgId

        // 恢复期间保持 loading=true，使思考 spinner 与工具"执行中"状态与正常流一致
        const _resumeIdx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (_resumeIdx !== -1) {
          setReactive(this.messages, _resumeIdx, { ...this.messages[_resumeIdx], loading: true })
        }
        // 注意：人工介入（批准/拒绝）不再作为用户侧消息展示，
        // 直接在模型侧的 execute 工具上进行了 HITL 标注。
        await resumeStream(sessionId, threadId, decisions, onChunk, controller.signal, assistantMsgId)

        await this.loadSessions()
      } catch (e) {
        if (e.name === 'AbortError') return
        console.error('恢复执行失败:', e)
        const idx = this.messages.findIndex(m => m.id === assistantMsgId)
        if (idx !== -1) {
          this.messages[idx].loading = false
          this.messages[idx].error = e.message || '恢复执行失败'
          if (e.status === 409) {
            this.messages[idx].pending_approval = null
          }
          setReactive(this.messages, idx, { ...this.messages[idx] })
        }
      } finally {
        // 如果 resumeStream 期间收到了新的 approval_required（onChunk 重新设置了
        // pendingApproval），则不清除，保持 isStreaming=true 等待用户审批
        const hasNewApproval = this.pendingApproval && this.pendingApproval !== currentApproval
        if (!hasNewApproval) {
          this.pendingApproval = null
          this.currentAbortController = null
          this.streamingAssistantId = null
          const sid = sessionId
          if (sid && this.sessionStates[sid]) {
            this.sessionStates[sid].isStreaming = false
            this.sessionStates[sid].abortController = null
          }
          this.unmarkStreaming(sid)
          this.saveCurrentSessionState()
          // 审批恢复流结束：以服务端历史为准回填，保证完整渲染
          await this.syncSessionFromServer(sid)
          this.recomputePendingApprovalFlag(sid)
          await this.loadSessions()
        }
      }
    },
    handleRetry(content) {
      // 直接重新发送消息内容，不经过输入框
      console.log('[handleRetry] content type:', typeof content, 'value:', content)

      if (content && typeof content === 'string' && content.trim()) {
        // 检查是否正在流式输出
        if (this.isStreaming) {
          this.error = '请等待当前消息完成'
          return
        }

        // 确保有当前会话
        if (!this.currentSessionId) {
          this.error = '请先创建会话'
          return
        }

        // 直接调用发送
        this.handleSendMessage(content.trim())
      }
    },
    handleStop() {
      // Mark current assistant message as no longer loading (stops tool spinning)
      if (this.streamingAssistantId) {
        const idx = this.messages.findIndex(m => m.id === this.streamingAssistantId)
        if (idx !== -1) {
          setReactive(this.messages, idx, { ...this.messages[idx], loading: false })
        }
      }
      this.streamingAssistantId = null

      // 停止按钮仅在当前会话正在流式时显示，取消的会话即当前会话
      const sid = this.currentSessionId

      // 中止正在进行的 fetch 请求（关闭 SSE 连接）
      if (this.currentAbortController) {
        this.currentAbortController.abort()
        this.currentAbortController = null
      }

      // 更新会话缓存状态
      if (sid && this.sessionStates[sid]) {
        this.sessionStates[sid].isStreaming = false
        this.sessionStates[sid].abortController = null
      }
      this.unmarkStreaming(sid)
      delete this.attachedStreamingSessions[sid]

      // 通知后端取消正在运行的流式任务（中断 astream 执行）并清除 Agent 缓存
      if (sid) {
        authFetch(`${API_BASE_URL}/agent/chat/cancel?session_id=${encodeURIComponent(sid)}`, {
          method: 'POST',
        }).catch(() => {})
      }
      this.error = '已停止生成'
      setTimeout(() => {
        this.error = null
      }, 2000)
    },
    async handleRemoveFile(message, messageIndex, file) {
      try {
        // 调用后端的删除文件接口
        await deleteFile(this.currentSessionId, file)

        // 更新前端的消息列表，移除已删除的文件
        if (this.messages[messageIndex]) {
          const updatedMessage = {
            ...this.messages[messageIndex],
            files: this.messages[messageIndex].files.filter(f => f.filename !== file.filename)
          }
          this.messages.splice(messageIndex, 1, updatedMessage)
        }

        // 显示删除成功的提示
        this.error = '文件删除成功'
        setTimeout(() => {
          this.error = null
        }, 2000)
      } catch (err) {
        console.error('文件删除失败:', err)
        this.error = '文件删除失败'
        setTimeout(() => {
          this.error = null
        }, 2000)
      }
    }
  },
}
