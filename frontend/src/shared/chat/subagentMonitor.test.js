// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chat/subagentMonitor.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import test from 'node:test'
import assert from 'node:assert/strict'

import { extractSubagentTasks, formatSubagentDuration, summarizeSubagentPill } from './subagentMonitor.js'

test('extractSubagentTasks extracts running and completed task blocks with steps', () => {
  const messages = [
    { role: 'user', content: '调研两个方案' },
    {
      role: 'assistant',
      loading: true,
      blocks: [
        { type: 'thinking', content: '拆分任务' },
        {
          type: 'tool_call',
          tool_name: 'task',
          tool_call_id: 'call-1',
          arguments: { description: '调研方案 A\n详细说明', subagent_type: 'research' },
          duration: 3.4,
          success: true,
          result: '方案 A 结论',
          subagent_steps: [
            { stage: 'tool_result', tool_name: 'read_file', summary: '读取 a.md', success: true, step: 1 },
          ],
        },
        {
          type: 'tool_call',
          tool_name: 'task',
          tool_call_id: 'call-2',
          arguments: { description: '调研方案 B', subagent_type: 'general-purpose' },
          duration: null,
          subagent_steps: [
            { stage: 'tool_call', tool_name: 'web_search', summary: '联网搜索 LangGraph', success: null, step: 1 },
          ],
        },
      ],
    },
  ]

  const tasks = extractSubagentTasks(messages)
  assert.equal(tasks.length, 2)
  assert.equal(tasks[0].title, '调研方案 A')
  assert.equal(tasks[0].status, 'completed')
  assert.equal(tasks[0].durationLabel, '3.4秒')
  assert.equal(tasks[0].subagentType, 'research')
  assert.equal(tasks[0].steps.length, 1)

  assert.equal(tasks[1].status, 'running')
  assert.equal(tasks[1].latestSummary, '联网搜索 LangGraph')

  const pill = summarizeSubagentPill(tasks)
  assert.equal(pill.total, 2)
  assert.equal(pill.completed, 1)
  assert.equal(pill.running, 1)
  assert.equal(pill.hasRunning, true)
  assert.equal(pill.progressPercent, 50)
  assert.equal(pill.statusSummary, '1 进行中 · 1 已完成')
  assert.equal(pill.pillLabel, '2 个子智能体 · 联网搜索 LangGraph')
})

test('extractSubagentTasks treats historical non-loading task without duration as completed', () => {
  const tasks = extractSubagentTasks([
    {
      role: 'assistant',
      loading: false,
      blocks: [
        {
          type: 'tool_call',
          tool_name: 'task',
          tool_call_id: 'hist-1',
          arguments: { description: '历史子任务' },
          duration: null,
          result: '已完成',
        },
      ],
    },
  ])
  assert.equal(tasks.length, 1)
  assert.equal(tasks[0].status, 'completed')
  const pill = summarizeSubagentPill(tasks)
  assert.equal(pill.hasRunning, false)
  assert.equal(pill.statusSummary, '1 已完成')
  assert.equal(pill.pillLabel, '1 个子智能体')
})

test('formatSubagentDuration formats seconds, minutes, and hours', () => {
  assert.equal(formatSubagentDuration(3.42), '3.4秒')
  assert.equal(formatSubagentDuration(563), '9分23秒')
  assert.equal(formatSubagentDuration(4546), '1小时15分')
})
