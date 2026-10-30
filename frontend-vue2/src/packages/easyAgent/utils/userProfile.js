/**
 * Decide whether an authenticated profile can restore the application shell.
 *
 * `admin` is an ordinary authenticated profile here. Authorization for
 * administrator-only features remains enforced by their backend endpoints.
 */
export function isRestorableUserProfile(profile) {
  return Boolean(
    profile
    && typeof profile.username === 'string'
    && profile.username.trim()
  )
}
