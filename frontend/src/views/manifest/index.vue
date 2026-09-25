<template>
  <section class="page" data-module="manifest">
    <header class="page-head">
      <div>
        <h2>单证处理管理</h2>
        <p class="page-desc">维护单证，围绕单证编号、单证类型、关联航次、申报箱量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记单证</button>
        <button class="btn" type="button" @click="downloadTemplate">下载导入模板</button>
        <button class="btn" type="button" @click="triggerImport">批量导入</button>
        <button class="btn" type="button" @click="exportSelected">按航次打包导出</button>
        <button class="btn" type="button" @click="exportRows">导出单证处理清单</button>
        <input ref="fileInput" type="file" accept=".csv" hidden @change="handleFile" />
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div v-if="importMessage || importFailures.length" class="import-result">
      <p class="import-message">{{ importMessage }}</p>
      <table v-if="importFailures.length" class="data-table">
        <thead>
          <tr>
            <th>行号</th>
            <th>单证编号</th>
            <th>不合格原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="failure in importFailures" :key="failure.line">
            <td>{{ failure.line }}</td>
            <td>{{ failure.code }}</td>
            <td>{{ failure.reason }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th class="check-col">
            <input type="checkbox" :checked="allSelected" @change="toggleAll" />
          </th>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td class="check-col">
            <input type="checkbox" :checked="selectedIds.has(Number(row.id))" @change="toggleRow(Number(row.id))" />
          </td>
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无单证处理数据，可先登记单证</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条单证处理记录<template v-if="selectedIds.size">，已选中 {{ selectedIds.size }} 条</template></span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ImportFailure = { line: number; code: string; reason: string }

const ENDPOINT = '/api/manifest'
const columns = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员", "单证状态"]
// 导入模板列：与后端 IMPORT_FIELDS 保持一致，导入时按列头匹配，不要求列顺序。
const importFields = columns.slice(0, 7)
const actions = ["提交单证", "审核通过", "退回单证"]
const statuses = ["待提交", "已提交", "已审核", "已退回"]
const stats = [{"label": "待提交单证", "value": 0}, {"label": "已提交单证", "value": 0}, {"label": "退回单证数", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const selectedIds = ref<Set<number>>(new Set())
const importMessage = ref('')
const importFailures = ref<ImportFailure[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

const allSelected = computed(() => rows.value.length > 0 && rows.value.every((row) => selectedIds.value.has(Number(row.id))))

function toggleAll() {
  selectedIds.value = allSelected.value ? new Set() : new Set(rows.value.map((row) => Number(row.id)))
}

function toggleRow(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  selectedIds.value = next
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function exportSelected() {
  if (!selectedIds.value.size) {
    errorMessage.value = '请先勾选要打包导出的单证'
    return
  }
  errorMessage.value = ''
  window.open(`${ENDPOINT}/export?ids=${[...selectedIds.value].join(',')}`, '_blank')
}

function openCreate() {
  errorMessage.value = '单证登记入口尚未接入审批流'
}

function downloadTemplate() {
  const sample = 'MANI-0100,进口舱单,VOYA-0001,120,张三,2026-09-25,李四'
  const blob = new Blob([`\uFEFF${importFields.join(',')}\n${sample}\n`], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = '单证导入模板.csv'
  link.click()
  URL.revokeObjectURL(link.href)
}

function triggerImport() {
  fileInput.value?.click()
}

function parseCsv(text: string): string[][] {
  const table: string[][] = []
  let field = ''
  let row: string[] = []
  let inQuotes = false
  const source = text.replace(/^\uFEFF/, '')
  const pushRow = () => {
    row.push(field)
    field = ''
    if (row.some((cell) => cell.trim() !== '')) {
      table.push(row)
    }
    row = []
  }
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (inQuotes) {
      if (char === '"') {
        if (source[index + 1] === '"') {
          field += '"'
          index += 1
        } else {
          inQuotes = false
        }
      } else {
        field += char
      }
    } else if (char === '"') {
      inQuotes = true
    } else if (char === ',') {
      row.push(field)
      field = ''
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && source[index + 1] === '\n') {
        index += 1
      }
      pushRow()
    } else {
      field += char
    }
  }
  if (field !== '' || row.length) {
    pushRow()
  }
  return table
}

async function handleFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  errorMessage.value = ''
  importMessage.value = ''
  importFailures.value = []
  try {
    const table = parseCsv(await file.text())
    const header = (table[0] ?? []).map((cell) => cell.trim())
    const missing = importFields.filter((field) => !header.includes(field))
    if (missing.length) {
      throw new Error(`模板列头不完整，缺少：${missing.join('、')}`)
    }
    const records = table.slice(1).map((cells) => {
      const record: Record<string, string> = {}
      for (const field of importFields) {
        record[field] = (cells[header.indexOf(field)] ?? '').trim()
      }
      return record
    })
    if (!records.length) {
      throw new Error('文件里没有可导入的单证行')
    }
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ rows: records }),
    })
    if (!response.ok) {
      throw new Error('批量导入请求未生效，请稍后重试')
    }
    const payload = await response.json()
    importMessage.value = payload.message ?? ''
    importFailures.value = payload.failed ?? []
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量导入失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('单证处理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '单证处理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('单证列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '单证处理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.import-result {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.import-message {
  margin: 0 0 8px;
  font-size: 13px;
}
.check-col {
  width: 32px;
  text-align: center;
}
</style>
