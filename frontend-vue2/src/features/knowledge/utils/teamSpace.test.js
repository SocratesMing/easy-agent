import test from 'node:test'
import assert from 'node:assert/strict'

import {
  canEditPermissionRole,
  canRemovePermission,
  defaultTeamDepartmentId,
  teamSpaceEmptyHint,
  teamSpaceOptionSuffix,
} from './teamSpace.js'

test('admin never sees a self-authorization prompt', () => {
  const management = { is_admin: true, can_create: false, department_id: null }
  assert.equal(teamSpaceOptionSuffix(management), '（暂无可用部门）')
  assert.equal(teamSpaceEmptyHint(management), '请先在人员与部门中配置可用部门')
})

test('only admin edits elevated roles on a team knowledge base', () => {
  const teamBase = { visibility: 'team' }
  assert.equal(canEditPermissionRole(teamBase, { is_admin: true }), true)
  assert.equal(canEditPermissionRole(teamBase, { is_admin: false }), false)
  assert.equal(
    canEditPermissionRole({ visibility: 'shared' }, { is_admin: false }),
    true,
  )
  assert.equal(
    canRemovePermission(teamBase, { is_admin: false }, { role: 'manager' }),
    false,
  )
  assert.equal(
    canRemovePermission(teamBase, { is_admin: false }, { role: 'viewer' }),
    true,
  )
})

test('regular account prompt distinguishes missing grant from missing department', () => {
  assert.equal(
    teamSpaceOptionSuffix({ can_create: false, department_id: 'dept-market' }),
    '（需 admin 授权）',
  )
  assert.equal(
    teamSpaceOptionSuffix({ can_create: false, department_id: null }),
    '（需配置部门）',
  )
})

test('admin department defaults to own valid department then first available department', () => {
  const departments = [{ id: 'dept-risk' }, { id: 'dept-market' }]
  assert.equal(
    defaultTeamDepartmentId({ department_id: 'dept-market', departments }),
    'dept-market',
  )
  assert.equal(
    defaultTeamDepartmentId({ department_id: null, departments }),
    'dept-risk',
  )
})
