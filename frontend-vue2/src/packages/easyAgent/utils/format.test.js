import assert from 'node:assert/strict'
import test from 'node:test'

import { formatRelativeDate } from './format.js'

// 固定「现在」= 2026-10-15 12:00:00（本地时区），避免用例随真实时间漂移
const NOW = new Date(2026, 9, 15, 12, 0, 0).getTime()
const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const ago = ms => new Date(NOW - ms).toISOString()

test('returns — for empty or invalid times', () => {
  assert.equal(formatRelativeDate(null, NOW), '—')
  assert.equal(formatRelativeDate('', NOW), '—')
  assert.equal(formatRelativeDate('not-a-date', NOW), '—')
})

test('uses relative wording within 30 days', () => {
  assert.equal(formatRelativeDate(ago(5 * MINUTE), NOW), '5分钟前')
  assert.equal(formatRelativeDate(ago(3 * HOUR), NOW), '3小时前')
  assert.equal(formatRelativeDate(ago(DAY), NOW), '昨天')
  assert.equal(formatRelativeDate(ago(2 * DAY), NOW), '前天')
  assert.equal(formatRelativeDate(ago(30 * DAY), NOW), '30天前')
})

test('falls back to the date beyond 30 days', () => {
  assert.equal(formatRelativeDate(ago(31 * DAY), NOW), '2026年9月14日')
})
