/** @fileoverview Configure model roles from the shared public catalog. */

/**
 * @param {string} id Existing page element ID.
 * @return {!HTMLElement} Page element.
 */
const getElement = (id) => document.getElementById(id);
const roleSelect = getElement('roleSelect');
const providerSelect = getElement('providerSelect');
let roles = {};
let providers = [];
let settingsGeneration = 0;
let saveGeneration = 0;
let configurationEdited = false;

/** Invalidate pending results when the view or configuration changes. */
function invalidateSettings() {
  settingsGeneration++;
  getElement('settingsResult').textContent = '';
}

/** @return {Object|undefined} Selected public provider preset. */
function currentPreset() {
  return providers.find((item) => item.id === providerSelect.value);
}
/** Fill role-appropriate options, then restore the saved selection. */
function fillProviders() {
  const previous = providerSelect.value;
  providerSelect.replaceChildren();
  const choices =
      roleSelect.value === 'image'
          ? providers.filter((item) => item.id === 'openai')
          : providers;
  for (const provider of choices) {
    const option = document.createElement('option');
    option.value = provider.id;
    option.textContent = provider.name;
    providerSelect.appendChild(option);
  }
  if (choices.some((item) => item.id === previous)) {
    providerSelect.value = previous;
  }
  if (roles[roleSelect.value]?.provider) {
    providerSelect.value = roles[roleSelect.value].provider;
  }
  showProvider();
}
/** Show public configuration without retrieving a stored credential. */
function showProvider() {
  const preset = currentPreset();
  if (!preset) {
    return;
  }
  const saved =
      roles[roleSelect.value]?.provider === preset.id
          ? roles[roleSelect.value]
          : null;
  getElement('baseUrl').value = saved?.baseUrl ?? preset.baseUrl;
  getElement('modelName').value =
      saved?.model ??
      (roleSelect.value === 'image' ? 'gpt-image-2.5-sunburst' : preset.model);
  getElement('apiKey').value = '';
  getElement('apiKey').placeholder = saved?.hasKey
      ? '已保存 SK；留空保持不变'
      : '填写此角色的 SK';
  getElement('keyHint').textContent = saved?.hasKey
      ? `SK 已保存在本机进程内存中；${saved.tested ? '图片输入/输出测试已通过' : '尚未通过图片能力测试'}`
      : 'SK 只存于本机服务内存；重启后需重填。';
  getElement('capabilityHint').textContent =
      roleSelect.value === 'image'
          ? '此角色将用 OpenAI Images Edits 接口和商品原图生成图片。模型 ID 需支持图片编辑。'
          : '此角色将把商品原图作为图片内容发送到所选模型。模型 ID 需支持视觉输入。';
  getElement('providerDocs').href =
      roleSelect.value === 'image'
          ? 'https://developers.openai.com/api/docs/guides/image-generation'
          : preset.docs;
  getElement('settingsResult').textContent = '';
}
roleSelect.addEventListener('change', () => {
  invalidateSettings();
  configurationEdited = false;
  fillProviders();
});
providerSelect.addEventListener('change', () => {
  invalidateSettings();
  configurationEdited = true;
  showProvider();
});
for (const id of ['baseUrl', 'modelName', 'apiKey']) {
  getElement(id).addEventListener('input', () => {
    invalidateSettings();
    configurationEdited = true;
  });
}
getElement('toggleKey').addEventListener('click', () => {
  const field = getElement('apiKey');
  field.type = field.type === 'password' ? 'text' : 'password';
  getElement('toggleKey').textContent = field.type === 'password' ? '显示' : '隐藏';
});
/** @return {!Promise<boolean>} Whether the role configuration was saved. */
async function save() {
  const generation = ++settingsGeneration;
  const requestGeneration = ++saveGeneration;
  const configuration = {
    role: roleSelect.value,
    provider: providerSelect.value,
    baseUrl: getElement('baseUrl').value.trim(),
    model: getElement('modelName').value.trim(),
    apiKey: getElement('apiKey').value.trim(),
  };
  const result = getElement('settingsResult');
  result.textContent = '正在保存…';
  try {
    const response = await fetch('/api/roles', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(configuration),
    });
    const data = await response.json();
    if (requestGeneration !== saveGeneration) {
      return false;
    }
    if (!response.ok) {
      throw new Error(data.error || '保存失败');
    }
    roles = data.roles;
    if (generation !== settingsGeneration) {
      if (!configurationEdited) {
        fillProviders();
      }
      return false;
    }
    configurationEdited = false;
    showProvider();
    result.textContent = '设置已保存。请运行图片能力测试。';
    return true;
  } catch (error) {
    if (generation === settingsGeneration) {
      result.textContent = `保存失败：${error.message}`;
    }
    return false;
  }
}
getElement('saveConfig').addEventListener('click', save);
getElement('testConfig').addEventListener('click', async () => {
  const role = roleSelect.value;
  if (!(await save())) {
    return;
  }
  const generation = settingsGeneration;
  const requestGeneration = saveGeneration;
  const configuration = roles[role];
  const result = getElement('settingsResult');
  result.textContent = '正在用测试图片验证模型能力，可能产生费用…';
  try {
    const response = await fetch('/api/roles/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role}),
    });
    const data = await response.json();
    if (requestGeneration !== saveGeneration) {
      return;
    }
    // An old success may no longer match the backend profile identity.
    const latestResponse = await fetch('/api/roles');
    if (!latestResponse.ok) {
      throw new Error('无法读取最新设置。');
    }
    const latestRoles = (await latestResponse.json()).roles || {};
    if (requestGeneration !== saveGeneration) {
      return;
    }
    roles = latestRoles;
    if (generation !== settingsGeneration) {
      return;
    }
    if (!response.ok) {
      throw new Error(
          `${data.code ? `[${data.code}] ` : ''}${data.error || '连接失败'}`,
      );
    }
    const latest = roles[role];
    if (
      !latest?.tested ||
        latest.provider !== configuration.provider ||
        latest.baseUrl !== configuration.baseUrl ||
        latest.model !== configuration.model
    ) {
      showProvider();
      result.textContent = '设置已变更，请重新运行图片能力测试。';
      return;
    }
    getElement('keyHint').textContent =
        `图片能力测试已通过，实际响应模型：${data.responseModel}`;
    result.textContent = `测试成功：${data.responseModel}`;
  } catch (error) {
    if (generation === settingsGeneration) {
      result.textContent = `测试失败：${error.message}`;
    }
  }
});
/**
 * Load the same non-secret presets used by the Python service.
 * @return {!Promise<void>} Resolves after settings initialize.
 */
async function initialize() {
  const requestGeneration = saveGeneration;
  try {
    const catalogResponse = await fetch('providers.json');
    if (!catalogResponse.ok) {
      throw new Error('无法连接本机服务。');
    }
    providers = await catalogResponse.json();
    const response = await fetch('/api/roles');
    if (!response.ok) {
      throw new Error('无法连接本机服务。');
    }
    const initialRoles = (await response.json()).roles || {};
    if (requestGeneration !== saveGeneration) {
      return;
    }
    roles = initialRoles;
    if (!configurationEdited) {
      fillProviders();
    }
  } catch {
    if (requestGeneration !== saveGeneration) {
      return;
    }
    if (!configurationEdited) {
      fillProviders();
    }
    getElement('settingsResult').textContent = '无法连接本机服务。';
  }
}
initialize();

export {};
