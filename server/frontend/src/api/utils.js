export const extractDistinctValues = (objects, property) => {
  const propertyValues = objects.map((object) => object[property])
  const distinctPropertyValues = [...new Set(propertyValues)]
  return distinctPropertyValues.map((value) => ({ [property]: value }))
}

export function generateCopyName(originalName) {
  if (!originalName) return null
  const cleanedName = originalName.replace(/\s+/g, ' ').trim()

  const namePattern = cleanedName.match(/(.*\sCopy)(?:\((\d+)\))?$/)
  if (namePattern) {
    const baseName = namePattern[1]
    const copyNum = namePattern[2]
    if (copyNum) {
      return `${baseName}(${parseInt(copyNum) + 1})`
    } else {
      return `${baseName}(1)`
    }
  } else {
    return `${cleanedName} Copy`
  }
}

/**
 * Extract a human-readable message from a failed API call.
 *
 * The backend's error responses carry the message in `response.data.error`;
 * network-level failures (no response) only have `error.message`.
 *
 * @param {*} error the caught error (usually an axios error)
 * @param {string} fallback message to use when neither source is available
 * @returns {string} the message to show the user
 */
export function getApiErrorMessage(error, fallback = 'An error occurred') {
  return error?.response?.data?.error || error?.message || fallback
}

/**
 * Whether a failed call was refused because it needs a freshly presented
 * second-factor code.
 *
 * Matched on the machine-readable code rather than the status: it is a 403
 * shared with ordinary permission failures, and prose is not a contract.
 *
 * @param {*} error the caught error (usually an axios error)
 * @returns {boolean} true when the caller should prompt for a code and retry
 */
export function needsMfaReauth(error) {
  return error?.response?.data?.detail?.code === 'mfa_reauth_required'
}

export function snakeToCamel(snakeCaseStr) {
  return snakeCaseStr.replace(/(_\w)/g, (match) => {
    return match[1].toUpperCase()
  })
}
