/** @fileoverview Upload originals and configure product generation. */

const modules = [
  ['首屏主视觉', '传递核心价值'],
  ['核心卖点图', '突出差异优势'],
  ['使用场景图', '呈现真实使用场景'],
  ['多角度图', '多角度呈现外观'],
  ['场景氛围图', '展示使用场景'],
  ['商品细节图', '放大材质与工艺'],
  ['品牌故事图', '传达品牌理念'],
  ['尺寸/容量/尺码图', '展示规格信息'],
  ['效果对比图', '使用前后效果对比'],
  ['详细规格/参数表', '展示详细商品数据'],
  ['工艺制作图', '展示工艺制作过程'],
  ['配件/赠品图', '明确收货的所有物品'],
  ['系列展示图', '多色或多SKU展示'],
  ['商品成分图', '展示配方/材质/成分'],
  ['售后保障图', '说明质保退换政策'],
  ['使用建议图', '商品使用的注意事项'],
];

const state = {
  images: [],
  draftId: localStorage.getItem('productDraftId') || '',
};
/**
 * @param {string} id Existing page element ID.
 * @return {!HTMLElement} Page element.
 */
const getElement = (id) => document.getElementById(id);
const moduleGrid = getElement('moduleGrid');
const input = getElement('imageInput');
const zone = getElement('uploadZone');
let toastTimer;

modules.forEach(([name, desc], index) => {
  const card = document.createElement('label');
  card.className = `module-card${index < 6 ? ' selected' : ''}`;
  card.innerHTML = `<input type="checkbox" ${index < 6 ? 'checked' : ''}><span class="module-copy"><span class="module-name"></span><span class="module-desc"></span></span>`;
  card.querySelector('.module-name').textContent = name;
  card.querySelector('.module-desc').textContent = desc;
  card.querySelector('input').addEventListener('change', (e) => {
    card.classList.toggle('selected', e.target.checked);
    updateModelStatus();
  });
  moduleGrid.appendChild(card);
});

/** @param {string} message Notice to display briefly. */
function toast(message) {
  const el = getElement('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

/** Render original previews and reflect the current upload count. */
function updateImages() {
  const grid = getElement('imageGrid');
  grid.replaceChildren();
  getElement('uploadEmpty').hidden = state.images.length > 0;
  grid.hidden = state.images.length === 0;
  state.images.forEach((image, index) => {
    const tile = document.createElement('div');
    tile.className = 'image-tile';
    const img = document.createElement('img');
    img.src = image.url;
    img.alt = `产品图 ${index + 1}`;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', `删除产品图 ${index + 1}`);
    remove.addEventListener('click', async (event) => {
      event.stopPropagation();
      try {
        const response = await fetch(
            `/api/drafts/${state.draftId}/images/${image.assetId}`,
            {method: 'DELETE'},
        );
        if (!response.ok) {
          throw new Error((await response.json()).error || '删除失败');
        }
        state.images.splice(index, 1);
        updateImages();
      } catch (error) {
        toast(error.message);
      }
    });
    tile.append(img, remove);
    grid.appendChild(tile);
  });
  if (state.images.length > 0 && state.images.length < 6) {
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'add-tile';
    add.textContent = '+';
    add.setAttribute('aria-label', '继续上传图片');
    grid.appendChild(add);
  }
  const button = getElement('generateButton');
  button.textContent = state.images.length ? '生成商品详情页' : '请上传产品图';
  button.classList.toggle('ready', state.images.length > 0);
  updateModelStatus();
}

/** @return {!Promise<string>} Existing or newly created local draft ID. */
async function ensureDraft() {
  if (state.draftId) {
    return state.draftId;
  }
  const response = await fetch('/api/drafts', {method: 'POST'});
  if (!response.ok) {
    throw new Error('无法创建本机草稿');
  }
  state.draftId = (await response.json()).draftId;
  localStorage.setItem('productDraftId', state.draftId);
  return state.draftId;
}

/**
 * @param {!FileList|!Array<!File>} files Originals selected or dropped.
 * @return {!Promise<void>} Resolves after upload attempts finish.
 */
async function addFiles(files) {
  try {
    const draftId = await ensureDraft();
    for (const file of files) {
      if (state.images.length >= 6) {
        toast('同一产品最多上传 6 张图片');
        break;
      }
      const form = new FormData();
      form.append('image', file);
      const response = await fetch(`/api/drafts/${draftId}/images`, {
        method: 'POST',
        body: form,
      });
      const data = await response.json();
      if (!response.ok) {
        toast(`${file.name}：${data.error || '上传失败'}`);
        continue;
      }
      state.images.push({assetId: data.assetId, url: data.previewUrl});
      updateImages();
    }
  } catch (error) {
    toast(error.message);
  }
  input.value = '';
  updateImages();
}

if (state.draftId) {
  fetch(`/api/drafts/${state.draftId}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (data) {
          state.images = data.images.map((image) => ({
            assetId: image.assetId,
            url: image.previewUrl,
          }));
          updateImages();
        } else {
          state.draftId = '';
          localStorage.removeItem('productDraftId');
        }
      })
      .catch(() => toast('本机草稿暂时无法加载'));
}

zone.addEventListener('click', () => {
  if (state.images.length < 6) {
    input.click();
  }
});
zone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    if (state.images.length < 6) {
      input.click();
    }
  }
});
input.addEventListener('change', (e) => addFiles(e.target.files));
zone.addEventListener('dragover', (e) => {
  e.preventDefault();
  zone.classList.add('dragging');
});
zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
zone.addEventListener('drop', (e) => {
  e.preventDefault();
  zone.classList.remove('dragging');
  addFiles(e.dataTransfer.files);
});

getElement('expandButton').addEventListener('click', (e) => {
  const expanded = e.currentTarget.getAttribute('aria-expanded') === 'true';
  e.currentTarget.setAttribute('aria-expanded', String(!expanded));
  e.currentTarget.firstChild.textContent = expanded ? '展开 ' : '收起 ';
  getElement('advancedSettings').hidden = expanded;
});

getElement('aiButton').addEventListener('click', async () => {
  const text = getElement('sellingPoints');
  const current = text.value.trim();
  if (!state.images.length) {
    toast('请先上传商品原图');
    return;
  }
  const button = getElement('aiButton');
  button.disabled = true;
  button.lastChild.textContent = '生成中';
  try {
    const response = await fetch('/api/assist-copy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        draftId: state.draftId,
        source: current,
        language: getElement('language').value,
        platform: getElement('platform').value,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || '生成失败');
    }
    text.value = data.text;
    getElement('analysisInfo').textContent =
        `分析来源：${data.provider} · ${data.imageCount} 张原图。已知：${data.facts.join('；') || '待核实'}。待补充：${data.unknowns.join('；') || '无'}。文案可继续修改。`;
    text.focus();
    toast('文案已生成，请核对产品事实');
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.lastChild.textContent = 'AI 帮写';
  }
});

/**
 * Refresh public role summaries and the expected request count.
 * @return {!Promise<void>} Resolves after the status refresh.
 */
async function updateModelStatus() {
  try {
    const response = await fetch('/api/roles');
    const config = await response.json();
    const profile = config.roles?.vision;
    const imageProfile = config.roles?.image;
    const providerNames = {
      openai: 'OpenAI',
      anthropic: 'Claude',
      gemini: 'Gemini',
      deepseek: 'DeepSeek',
      qwen: '通义千问',
      kimi: 'Kimi',
      glm: 'GLM',
      siliconflow: '硅基流动',
      openrouter: 'OpenRouter',
      volcengine: '豆包',
      baidu: '百度千帆',
      tencent: '腾讯混元',
      custom: '自定义',
    };
    getElement('modelStatus').textContent = profile?.hasKey
        ? `视觉模型：${providerNames[profile.provider] || profile.provider} · ${profile.model}`
        : 'AI 帮写尚未配置视觉模型';
    getElement('sendNotice').textContent =
        `点击 AI 帮写后，将向 ${profile?.provider || '所选视觉模型'} 发送 ${state.images.length} 张商品原图与填写的资料；可能产生费用。`;
    const selectedCount = moduleGrid.querySelectorAll('input:checked').length;
    getElement('generationNotice').textContent =
        `点击生成后将把 ${state.images.length} 张原图与资料发送给 ${profile?.provider || '未配置的视觉模型'}，并为 ${selectedCount} 个模块调用 ${imageProfile?.provider || '未配置的生图模型'}；预计 ${1 + selectedCount * 2} 次模型请求，可能产生费用。`;
  } catch {
    getElement('modelStatus').textContent = '模型服务未启动';
  }
}
updateModelStatus();
window.addEventListener('pageshow', updateModelStatus);

document
    .querySelectorAll('.help')
    .forEach((button) =>
        button.addEventListener('click', () => toast(button.dataset.tip)),
    );

getElement('generateButton').addEventListener('click', async () => {
  if (!state.images.length) {
    input.click();
    return;
  }
  const selected = [...moduleGrid.querySelectorAll('input:checked')].map(
      (input) =>
          input.closest('.module-card').querySelector('.module-name').textContent,
  );
  if (!selected.length) {
    toast('请至少选择一个模块');
    return;
  }
  const brief = getElement('sellingPoints').value.trim();
  if (!brief) {
    toast('请先填写商品资料');
    getElement('sellingPoints').focus();
    return;
  }
  const button = getElement('generateButton');
  button.disabled = true;
  button.textContent = '正在创建任务…';
  try {
    const settings = Object.fromEntries(
        ['platform', 'market', 'language', 'quality', 'ratio', 'style'].map(
            (key) => [key, getElement(key).value],
        ),
    );
    const response = await fetch('/api/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        draftId: state.draftId,
        brief,
        settings,
        modules: selected,
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || '创建任务失败');
    }
    localStorage.setItem('lastProductJobId', data.jobId);
    location.href = `results.html?id=${encodeURIComponent(data.jobId)}`;
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = '生成商品详情页';
  }
});

export {};
