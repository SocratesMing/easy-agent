import { setReactive } from './reactiveSet.js'
import { applyStreamError as applyError } from '../shared/chat/streamError.js'

export { STREAM_TIMEOUT_CODE, STREAM_TIMEOUT_INTERRUPT_TEXT } from '../shared/chat/streamError.js'

export function applyStreamError(options) {
  return applyError(options, setReactive)
}
