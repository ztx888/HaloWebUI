import { createHash } from 'node:crypto'
import { lookup } from 'node:dns/promises'
import { readFile, stat, writeFile } from 'node:fs/promises'
import os from 'node:os'

const DEBUG_ENV_KEYS = [
  'PATH',
  'HOME',
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
  'NO_PROXY',
  'NODE_OPTIONS',
  'NODE_EXTRA_CA_CERTS',
  'NODE_TLS_REJECT_UNAUTHORIZED',
  'SSL_CERT_FILE',
  'SSL_CERT_DIR',
  'REQUESTS_CA_BUNDLE',
  'CURL_CA_BUNDLE'
]

async function loadManifest(path) {
  const raw = await readFile(path, 'utf8')
  return JSON.parse(raw)
}

async function writeResult(path, payload) {
  await writeFile(path, JSON.stringify(payload), 'utf8')
}

async function ensureBodyFile(path, content = '') {
  if (!path) {
    return
  }
  await writeFile(path, content, 'utf8')
}

function sha256Hex(value) {
  return createHash('sha256').update(value).digest('hex')
}

function summarizeString(value) {
  const text = String(value ?? '')
  return {
    length: text.length,
    sha256: sha256Hex(text)
  }
}

function redactHeaders(headers) {
  const redacted = {}
  for (const [key, value] of Object.entries(headers || {})) {
    const lower = String(key).toLowerCase()
    if (['authorization', 'api-key', 'x-api-key'].includes(lower)) {
      redacted[key] = '<redacted>'
    } else {
      redacted[key] = String(value)
    }
  }
  return redacted
}

function collectDebugEnv() {
  const result = {}
  for (const key of DEBUG_ENV_KEYS) {
    if (Object.prototype.hasOwnProperty.call(process.env, key)) {
      result[key] = process.env[key] || ''
    }
  }
  return result
}

async function summarizeFiles(files) {
  const summaries = []
  for (const file of files || []) {
    const path = String(file.path || '')
    const fileStat = path ? await stat(path) : null
    const fileBytes = path ? await readFile(path) : Buffer.alloc(0)
    summaries.push({
      field_name: file.field_name || 'file',
      filename: file.filename || 'file.bin',
      mime: file.mime || 'application/octet-stream',
      path,
      size: fileStat?.size ?? fileBytes.length,
      sha256: sha256Hex(fileBytes)
    })
  }
  return summaries
}

function summarizeFormFields(formFields) {
  const summary = {}
  for (const [key, value] of Object.entries(formFields || {})) {
    if (key === 'prompt') {
      summary[key] = summarizeString(value)
    } else {
      summary[key] = value
    }
  }
  return summary
}

async function collectDebug(manifest) {
  const url = new URL(manifest.url)
  let dnsLookup = null
  try {
    dnsLookup = await lookup(url.hostname, { all: true })
  } catch (error) {
    dnsLookup = {
      error_type: error?.constructor?.name || 'Error',
      error_message: String(error?.message || error)
    }
  }

  return {
    pid: process.pid,
    ppid: process.ppid,
    exec_path: process.execPath,
    node_version: process.version,
    undici_version: process.versions?.undici || null,
    cwd: process.cwd(),
    platform: process.platform,
    arch: process.arch,
    hostname: os.hostname(),
    env: collectDebugEnv(),
    manifest_summary: {
      url: manifest.url,
      request_kind: manifest.request_kind,
      headers: redactHeaders(manifest.headers || {}),
      form_fields: summarizeFormFields(manifest.form_fields || {}),
      files: await summarizeFiles(manifest.files || [])
    },
    dns_lookup: dnsLookup
  }
}

async function buildBody(manifest, headers) {
  if (manifest.request_kind === 'json') {
    if (!headers.has('content-type')) {
      headers.set('content-type', 'application/json')
    }
    return JSON.stringify(manifest.json_body || {})
  }

  if (manifest.request_kind === 'multipart') {
    headers.delete('content-type')
    headers.delete('content-length')

    const form = new FormData()
    for (const [key, value] of Object.entries(manifest.form_fields || {})) {
      if (value === undefined || value === null) {
        continue
      }
      form.append(key, String(value))
    }

    for (const file of manifest.files || []) {
      const fileBytes = await readFile(file.path)
      const blob = new Blob([fileBytes], {
        type: file.mime || 'application/octet-stream'
      })
      form.append(file.field_name || 'file', blob, file.filename || 'file.bin')
    }
    return form
  }

  throw new Error(`Unsupported request_kind: ${manifest.request_kind}`)
}

async function main() {
  const manifestPath = process.argv[2]
  const resultPath = process.argv[3]

  if (!manifestPath || !resultPath) {
    throw new Error('Usage: node openai-image-fetch.mjs <manifest-path> <result-path>')
  }

  const manifest = await loadManifest(manifestPath)
  const result = {
    status: null,
    headers: {},
    elapsed_ms: null,
    response_body_path: manifest.response_body_path || null,
    error_type: null,
    error_message: null
  }
  result.debug = await collectDebug(manifest)

  const startedAt = Date.now()
  try {
    const headers = new Headers(manifest.headers || {})
    const body = await buildBody(manifest, headers)

    const response = await fetch(manifest.url, {
      method: 'POST',
      headers,
      body,
      redirect: 'follow'
    })

    result.status = response.status
    result.headers = Object.fromEntries(response.headers.entries())
    result.elapsed_ms = Date.now() - startedAt

    const text = await response.text()
    await ensureBodyFile(result.response_body_path, text)
    await writeResult(resultPath, result)
    process.exit(0)
  } catch (error) {
    result.elapsed_ms = Date.now() - startedAt
    result.error_type = error?.constructor?.name || 'Error'
    result.error_message = String(error?.message || error)
    result.error_cause_type = error?.cause?.constructor?.name || null
    result.error_cause_code = error?.cause?.code || null
    result.error_cause_message = error?.cause?.message || null
    result.error_stack = error?.stack || null
    result.error_cause_stack = error?.cause?.stack || null
    await ensureBodyFile(result.response_body_path, '')
    await writeResult(resultPath, result)
    process.exit(0)
  }
}

main().catch(async (error) => {
  const resultPath = process.argv[3]
  if (resultPath) {
    await writeResult(resultPath, {
      status: null,
      headers: {},
      elapsed_ms: null,
      response_body_path: null,
      error_type: error?.constructor?.name || 'Error',
      error_message: String(error?.message || error),
      error_cause_type: error?.cause?.constructor?.name || null,
      error_cause_code: error?.cause?.code || null,
      error_cause_message: error?.cause?.message || null,
      error_stack: error?.stack || null,
      error_cause_stack: error?.cause?.stack || null
    })
  }
  process.exit(1)
})
