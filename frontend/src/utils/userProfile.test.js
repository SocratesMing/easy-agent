import assert from 'node:assert/strict'
import test from 'node:test'

import { isRestorableUserProfile } from './userProfile.js'

test('allows the authenticated admin profile to restore after refresh', () => {
  assert.equal(isRestorableUserProfile({ username: 'admin' }), true)
})

test('rejects missing and blank profile usernames', () => {
  assert.equal(isRestorableUserProfile(null), false)
  assert.equal(isRestorableUserProfile({}), false)
  assert.equal(isRestorableUserProfile({ username: '   ' }), false)
})
