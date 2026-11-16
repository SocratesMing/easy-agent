import test from 'node:test'
import assert from 'node:assert/strict'

import { filterPermissionUsers } from './permissions.js'

const users = [
  { user_id: 'u-1', display_name: '张三', username: 'zhangsan' },
  { user_id: 'u-2', display_name: '李四', username: 'lisi' },
]

test('permission search matches display name and username', () => {
  assert.deepEqual(
    filterPermissionUsers(users, '张').map(user => user.user_id),
    ['u-1']
  )
  assert.deepEqual(
    filterPermissionUsers(users, 'ZHANGSAN').map(user => user.user_id),
    ['u-1']
  )
  assert.deepEqual(
    filterPermissionUsers(users, '  lisi  ').map(user => user.user_id),
    ['u-2']
  )
})

test('permission search returns empty without a keyword or match', () => {
  assert.deepEqual(filterPermissionUsers(users, ''), [])
  assert.deepEqual(filterPermissionUsers(users, '不存在'), [])
})
