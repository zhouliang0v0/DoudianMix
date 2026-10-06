/** @fileoverview Display persisted jobs and their module results. */

/**
 * @param {string} id Existing page element ID.
 * @return {!HTMLElement} Page element.
 */
const getElement = (id) => document.getElementById(id);
const id = new URLSearchParams(location.search).get('id');
const stageNames = {
  queued: '排队中',
  analyzing: '正在分析原图',
  planning: '正在规划文案',
  generating: '正在生成成品图',
  waiting_credentials: '等待重新填写模型 SK',
  completed: '全部完成',
  partial: '部分完成',
  failed: '生成失败',
  canceled: '已取消',
};
let latest;
let polling;

/**
 * Create display content without interpreting provider text as HTML.
 * @param {string} tag HTML element name.
 * @param {string} text Visible content.
 * @param {string=} className Optional CSS class.
 * @return {!HTMLElement} Created element.
 */
function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) {
    node.className = className;
  }
  return node;
}
/**
 * @param {string} url Local job endpoint.
 * @param {string} method HTTP method.
 * @param {Object=} body Optional JSON payload.
 * @return {!Promise<Object>} Public response.
 */
async function action(url, method, body) {
  const response = await fetch(url, {
    method,
    ...(body
      ? {
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      }
      : {}),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || '操作失败');
  }
  return data;
}
/** @param {!Object} job Persisted job with the existing API field names. */
function render(job) {
  latest = job;
  getElement('jobView').hidden = false;
  getElement('jobTitle').textContent =
      job.brief.match(/产品名称[：:]\s*([^\n]+)/)?.[1] || '商品详情页';
  const completed = job.modules.filter(
      (item) => item.status === 'completed',
  ).length;
  const failed = job.modules.filter((item) => item.status === 'failed').length;
  getElement('jobStatus').textContent =
      `${stageNames[job.status] || job.status} · 已完成 ${completed}/${job.modules.length} · 失败 ${failed}${job.currentModule ? ` · 当前：${job.currentModule}` : ''}`;
  getElement('jobMeta').textContent =
      `视觉模型：${job.visionProfile.provider} / ${job.visionProfile.model}；生图模型：${job.imageProfile.provider} / ${job.imageProfile.model}；原图 ${job.images.length} 张。`;
  getElement('jobError').textContent =
      job.error ||
      (job.status === 'waiting_credentials'
          ? '服务重启或模型配置变更后，请在模型设置中重新填写任务创建时的模型和 SK。'
          : '');
  getElement('cancelJob').hidden = ![
    'queued',
    'analyzing',
    'planning',
    'generating',
    'waiting_credentials',
  ].includes(job.status);
  getElement('exportJob').href = `/api/jobs/${job.id}/export`;
  getElement('analysisFacts').textContent =
      `已知事实：${job.analysis?.facts?.join('；') || '分析中'}`;
  getElement('analysisUnknowns').textContent =
      `待补充：${job.analysis?.unknowns?.join('；') || '无'}`;
  const originals = getElement('originals');
  originals.replaceChildren();
  job.images.forEach((image, index) => {
    const img = document.createElement('img');
    img.src = `/api/jobs/${job.id}/originals/${image.assetId}`;
    img.alt = `原图 ${index + 1}`;
    originals.append(img);
  });
  const grid = getElement('resultModules');
  grid.replaceChildren();
  job.modules.forEach((item, index) => {
    const card = element('article', '', 'result-card');
    card.append(
        element('h4', `${String(index + 1).padStart(2, '0')} ${item.name}`),
        element(
            'p',
            stageNames[item.status] ||
                {pending: '等待中', running: '处理中', failed: '失败'}[item.status] ||
                item.status,
            'result-state',
        ),
    );
    if (item.imageUrl && item.status === 'completed') {
      const img = document.createElement('img');
      img.src = item.imageUrl;
      img.alt = `${item.name} 成品图`;
      card.append(img);
      const download = element('a', '下载此图');
      download.href = item.imageUrl;
      download.download = `${String(index + 1).padStart(2, '0')}-${item.name}.png`;
      card.append(download);
    }
    if (item.headline) {
      card.append(element('strong', item.headline));
    }
    if (item.body) {
      card.append(element('p', item.body));
    }
    if (item.error) {
      card.append(element('p', `失败原因：${item.error}`, 'result-error'));
    }
    if (item.status === 'failed') {
      const retry = element('button', '仅重试此模块');
      retry.type = 'button';
      retry.addEventListener('click', async () => {
        retry.disabled = true;
        try {
          await action(`/api/jobs/${job.id}/retry`, 'POST', {
            moduleId: item.id,
          });
          await load();
        } catch (error) {
          getElement('resultMessage').textContent = error.message;
          retry.disabled = false;
        }
      });
      card.append(retry);
    }
    grid.append(card);
  });
  if (['completed', 'partial', 'failed', 'canceled'].includes(job.status)) {
    clearInterval(polling);
    polling = null;
  } else if (!polling) {
    polling = setInterval(load, 1600);
  }
}
/**
 * Refresh a job; active jobs continue polling until a terminal status.
 * @return {!Promise<void>} Resolves after refreshing the view.
 */
async function load() {
  try {
    const response = await fetch(`/api/jobs/${id}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || '任务不存在');
    }
    render(data);
  } catch (error) {
    getElement('resultMessage').textContent = error.message;
  }
}
/**
 * Display persisted job summaries and their existing navigation targets.
 * @return {!Promise<void>} Resolves after populating history.
 */
async function loadList() {
  const response = await fetch('/api/jobs');
  const data = await response.json();
  getElement('jobList').hidden = false;
  getElement('jobList').append(element('h2', '历史任务'));
  for (const job of data.jobs || []) {
    const link = element(
        'a',
        `${new Date(job.createdAt).toLocaleString()} · ${stageNames[job.status] || job.status} · ${job.completed}/${job.modules} 模块`,
    );
    link.href = `results.html?id=${job.id}`;
    getElement('jobList').append(link);
  }
  if (!data.jobs?.length) {
    getElement('jobList').append(element('p', '还没有生成任务。'));
  }
}
getElement('cancelJob').addEventListener('click', async () => {
  try {
    await action(`/api/jobs/${id}/cancel`, 'POST');
    await load();
  } catch (error) {
    getElement('resultMessage').textContent = error.message;
  }
});
getElement('deleteJob').addEventListener('click', async () => {
  if (!confirm('删除此任务及所有本机原图、结果和文案？')) {
    return;
  }
  try {
    await action(`/api/jobs/${id}`, 'DELETE');
    if (localStorage.getItem('lastProductJobId') === id) {
      localStorage.removeItem('lastProductJobId');
    }
    if (localStorage.getItem('productDraftId') === latest?.draftId) {
      localStorage.removeItem('productDraftId');
    }
    location.href = 'results.html';
  } catch (error) {
    getElement('resultMessage').textContent = error.message;
  }
});
if (id) {
  load();
  polling = setInterval(load, 1600);
} else {
  loadList().catch((error) => {
    getElement('resultMessage').textContent = error.message;
  });
}

export {};
