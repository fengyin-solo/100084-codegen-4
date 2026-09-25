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
        <button class="btn" type="button" :disabled="!selectedIds.length" @click="exportPackage">
          按航次打包导出<template v-if="selectedIds.length">（{{ selectedIds.length }}）</template>
        </button>
        <button class="btn" type="button" @click="exportRows">导出单证处理清单</button>
        <input ref="importInput" type="file" accept=".csv,text/csv" hidden @change="handleImportFile" />
      </div>
    </header>

    <div v-if="importReport" class="import-report" :class="{ failed: !importReport.ok }">
      <p class="import-summary">{{ importReport.message }}</p>
      <ul v-if="importReport.errors.length" class="import-errors">
        <li v-for="item in importReport.errors" :key="item.line">
          第 {{ item.line }} 行<template v-if="item.key">（{{ item.key }}）</template>：{{ item.reason }}
        </li>
      </ul>
    </div>

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
            <input type="checkbox" :checked="isSelected(row)" @change="toggleRow(row)" />
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
      <span>共 {{ total }} 条单证处理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ImportReport = {
  ok: boolean
  message: string
  created: number
  failed: number
  duplicated_file: boolean
  errors: { line: number; key: string | null; reason: string }[]
}

const ENDPOINT = '/api/manifest'
const columns = ["单证编号", "单证类型", "关联航次", "申报箱量", "申报人", "提交时间", "审核人员", "单证状态"]
const actions = ["提交单证", "审核通过", "退回单证"]
const statuses = ["待提交", "已提交", "已审核", "已退回"]
const stats = [{"label": "待提交单证", "value": 0}, {"label": "已提交单证", "value": 0}, {"label": "退回单证数", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const selectedIds = ref<(string | number)[]>([])
const importReport = ref<ImportReport | null>(null)
const importInput = ref<HTMLInputElement | null>(null)

const allSelected = computed(() => rows.value.length > 0 && rows.value.every((row) => isSelected(row)))

function isSelected(row: Row) {
  return row.id != null && selectedIds.value.includes(row.id)
}

function toggleRow(row: Row) {
  if (row.id == null) return
  selectedIds.value = isSelected(row)
    ? selectedIds.value.filter((id) => id !== row.id)
    : [...selectedIds.value, row.id]
}

function toggleAll() {
  const pageIds = rows.value.map((row) => row.id).filter((id): id is string | number => id != null)
  selectedIds.value = allSelected.value
    ? selectedIds.value.filter((id) => !pageIds.includes(id))
    : [...new Set([...selectedIds.value, ...pageIds])]
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function downloadTemplate() {
  window.open(`${ENDPOINT}/template`, '_blank')
}

function triggerImport() {
  importInput.value?.click()
}

async function handleImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  errorMessage.value = ''
  importReport.value = null
  try {
    const content = await file.text()
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, content }),
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload?.detail ?? '单证导入失败，请稍后重试')
    }
    importReport.value = payload as ImportReport
    if (importReport.value.created > 0) {
      await reload()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '单证导入失败'
  }
}

async function exportPackage() {
  if (!selectedIds.value.length) {
    errorMessage.value = '请先勾选要导出的单证'
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/export/package?ids=${selectedIds.value.join(',')}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      throw new Error(payload?.detail ?? '按航次打包导出失败，请稍后重试')
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = '单证按航次打包.zip'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '按航次打包导出失败'
  }
}

function openCreate() {
  errorMessage.value = '单证登记入口尚未接入审批流'
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
.page-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.import-report {
  background: #f0f7ff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.import-report.failed {
  background: #fff7f5;
}
.import-summary {
  margin: 0;
}
.import-errors {
  margin: 6px 0 0;
  padding-left: 18px;
  color: #b42318;
}
.check-col {
  width: 32px;
  text-align: center;
}
</style>
