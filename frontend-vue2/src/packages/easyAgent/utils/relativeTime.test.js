import assert from 'node:assert/strict'
import test from 'node:test'

import { formatRelativeTime } from './relativeTime.js'

// 固定「现在」= 2026-10-15 12:00:00（本地时区），避免用例随真实时间漂移
const NOW = new Date(2026, 9, 15, 12, 0, 0).getTime()
const minutesAgo = n => NOW - n * 60 * 1000

test('shows 刚刚 within the first minute', () => {
  assert.equal(formatRelativeTime(minutesAgo(0), NOW), '刚刚')
  assert.equal(formatRelativeTime(minutesAgo(0.5), NOW), '刚刚')
})

test('shows minutes / hours / days compactly', () => {
  assert.equal(formatRelativeTime(minutesAgo(5), NOW), '5分钟前')
  assert.equal(formatRelativeTime(minutesAgo(59), NOW), '59分钟前')
  assert.equal(formatRelativeTime(minutesAgo(60), NOW), '1小时前')
  assert.equal(formatRelativeTime(minutesAgo(60 * 23), NOW), '23小时前')
  assert.equal(formatRelativeTime(minutesAgo(60 * 24), NOW), '1天前')
  assert.equal(formatRelativeTime(minutesAgo(60 * 24 * 6), NOW), '6天前')
})

test('falls back to dates beyond a week', () => {
  assert.equal(formatRelativeTime(minutesAgo(60 * 24 * 7), NOW), '10月8日')
  assert.equal(formatRelativeTime(new Date(2025, 7, 12).getTime(), NOW), '2025年8月12日')
})

test('treats clock skew as 刚刚 and empty input as blank', () => {
  assert.equal(formatRelativeTime(NOW + 30 * 1000, NOW), '刚刚')
  assert.equal(formatRelativeTime('', NOW), '')
  assert.equal(formatRelativeTime(null, NOW), '')
  assert.equal(formatRelativeTime('不是时间', NOW), '')
})
