/** @fileoverview Test the real settings script with controlled async I/O. */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'public/settings.js'), 'utf8');
const catalog = JSON.parse(
    fs.readFileSync(path.join(root, 'public/providers.json'), 'utf8'),
);

/** Minimal page elements; the production event handlers execute unchanged. */
class Element {
  constructor(value = '') {
    this.value = value;
    this.textContent = '';
    this.children = [];
    this.listeners = new Map();
  }

  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }

  replaceChildren() {
    this.children = [];
    this.value = '';
  }

  appendChild(child) {
    this.children.push(child);
    if (!this.value) {
      this.value = child.value;
    }
  }

  async dispatch(name) {
    await Promise.all(
        (this.listeners.get(name) || []).map((callback) => callback()),
    );
  }
}

/** @return {!Object} Public metadata fixture, excluding credentials. */
function initialRoles() {
  return {
    vision: {
      role: 'vision', provider: 'openai', baseUrl: 'https://example.test/v1',
      model: 'vision-original', tested: false, hasKey: true,
    },
    image: {
      role: 'image', provider: 'openai', baseUrl: 'https://example.test/v1',
      model: 'image-original', tested: false, hasKey: true,
    },
  };
}

/** @return {!Object} Deferred response for deliberately reordered requests. */
function deferred() {
  let resolve;
  const promise = new Promise((complete) => {
    resolve = complete;
  });
  return {promise, resolve};
}

/** @return {!Object} Fetch-compatible JSON response with a stable snapshot. */
function response(data, ok = true) {
  const snapshot = JSON.stringify(data);
  return {ok, json: async () => JSON.parse(snapshot)};
}

/** Wait for a concrete async boundary without wall-clock sleeps. */
async function until(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail('settings operation did not reach the expected boundary');
}

/** @return {!Promise<!Object>} Initialized page and controlled local API. */
async function page({pauseInitialization = false} = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      elements.set(id, new Element(id === 'roleSelect' ? 'vision' : ''));
    }
    return elements.get(id);
  };
  const state = {
    roles: initialRoles(),
    tests: [],
    reads: [],
    savesStarted: 0,
    pauseSave: null,
    pauseSaveProcessing: null,
    pauseReadResponses: pauseInitialization,
  };
  const fetch = async (url, options = {}) => {
    if (url === 'providers.json') {
      return response(catalog);
    }
    if (url === '/api/roles/test') {
      const pending = deferred();
      state.tests.push({role: JSON.parse(options.body).role, ...pending});
      return pending.promise;
    }
    assert.equal(url, '/api/roles');
    if (options.method === 'POST') {
      const config = JSON.parse(options.body);
      state.savesStarted++;
      if (state.pauseSaveProcessing) {
        await state.pauseSaveProcessing.promise;
      }
      state.roles[config.role] = {
        role: config.role, provider: config.provider, baseUrl: config.baseUrl,
        model: config.model, hasKey: true, tested: false,
      };
      const saved = response({roles: state.roles});
      if (state.pauseSave) {
        await state.pauseSave.promise;
      }
      return saved;
    }
    const snapshot = response({roles: state.roles});
    if (state.pauseReadResponses) {
      const pending = deferred();
      state.reads.push({snapshot, ...pending});
      return pending.promise;
    }
    return snapshot;
  };
  const initializing = vm.runInNewContext(source.replace(/^export \{\};\s*$/m, ''), {
    document: {getElementById: element, createElement: () => new Element()},
    fetch,
  });
  await until(() => pauseInitialization ? state.reads.length === 1 :
      element('modelName').value === 'vision-original');
  const change = async (id, value, event = 'change') => {
    element(id).value = value;
    await element(id).dispatch(event);
  };
  const begin = () => element('testConfig').dispatch('click');
  const complete = (index, success = true) => {
    const request = state.tests[index];
    if (success) {
      state.roles[request.role].tested = true;
    }
    request.resolve(response(success ? {
      ok: true, role: request.role, responseModel: 'actual-' + request.role,
    } : {error: 'synthetic capability failure', code: 'capability'}, success));
  };
  return {element, state, change, begin, complete, initializing};
}

for (const success of [true, false]) {
  test(`role switch ignores old test ${success ? 'success' : 'failure'}`, async () => {
    const ui = await page();
    const pending = ui.begin();
    await until(() => ui.state.tests.length === 1);
    assert.equal(ui.state.tests[0].role, 'vision');
    await ui.change('roleSelect', 'image');
    const hint = ui.element('keyHint').textContent;
    ui.complete(0, success);
    await pending;
    assert.equal(ui.element('settingsResult').textContent, '');
    assert.equal(ui.element('keyHint').textContent, hint);
    await ui.change('roleSelect', 'vision');
    await ui.change('roleSelect', 'image');
    assert.match(ui.element('keyHint').textContent, /尚未通过/);
  });

  test(`provider switch ignores old test ${success ? 'success' : 'failure'}`, async () => {
    const ui = await page();
    const pending = ui.begin();
    await until(() => ui.state.tests.length === 1);
    await ui.change('providerSelect', 'anthropic');
    const model = ui.element('modelName').value;
    const hint = ui.element('keyHint').textContent;
    ui.complete(0, success);
    await pending;
    assert.equal(ui.element('providerSelect').value, 'anthropic');
    assert.equal(ui.element('modelName').value, model);
    assert.equal(ui.element('keyHint').textContent, hint);
    assert.equal(ui.element('settingsResult').textContent, '');
  });

  test(`save during test ignores old ${success ? 'success' : 'failure'}`, async () => {
    const ui = await page();
    const pending = ui.begin();
    await until(() => ui.state.tests.length === 1);
    // Identical public values still create a new backend profile identity.
    await ui.element('saveConfig').dispatch('click');
    const message = ui.element('settingsResult').textContent;
    // The backend identity guard leaves this replacement untested.
    ui.state.tests[0].resolve(response(success ? {
      ok: true, role: 'vision', responseModel: 'old-profile',
    } : {error: 'old profile failed'}, success));
    await pending;
    assert.equal(ui.element('settingsResult').textContent, message);
    assert.match(ui.element('keyHint').textContent, /尚未通过/);
    await ui.change('roleSelect', 'image');
    await ui.change('roleSelect', 'vision');
    assert.match(ui.element('keyHint').textContent, /尚未通过/);
  });
}

test('current success uses authoritative tested role state', async () => {
  const ui = await page();
  const pending = ui.begin();
  await until(() => ui.state.tests.length === 1);
  ui.complete(0);
  await pending;
  assert.equal(ui.element('settingsResult').textContent, '测试成功：actual-vision');
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  assert.match(ui.element('keyHint').textContent, /测试已通过/);
});

test('successful response cannot mark an authoritative untested profile', async () => {
  const ui = await page();
  const pending = ui.begin();
  await until(() => ui.state.tests.length === 1);
  ui.state.tests[0].resolve(response({ok: true, role: 'vision', responseModel: 'old'}));
  await pending;
  assert.doesNotMatch(ui.element('settingsResult').textContent, /测试成功/);
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  assert.match(ui.element('keyHint').textContent, /尚未通过/);
});

test('current failure remains visible and untested', async () => {
  const ui = await page();
  const pending = ui.begin();
  await until(() => ui.state.tests.length === 1);
  ui.complete(0, false);
  await pending;
  assert.match(ui.element('settingsResult').textContent, /测试失败.*capability/);
  assert.match(ui.element('keyHint').textContent, /尚未通过/);
});

test('role switch during save never tests the newly selected role', async () => {
  const ui = await page();
  ui.state.pauseSave = deferred();
  const pending = ui.begin();
  await until(() => ui.element('settingsResult').textContent === '正在保存…');
  await ui.change('roleSelect', 'image');
  ui.state.pauseSave.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  if (ui.state.tests.length) {
    ui.complete(0);
  }
  await pending;
  assert.equal(ui.state.tests.length, 0);
  assert.equal(ui.element('settingsResult').textContent, '');
});

test('a saved role remains current after navigating during its save', async () => {
  const ui = await page();
  await ui.change('modelName', 'saved-replacement', 'input');
  ui.state.pauseSave = deferred();
  const saving = ui.element('saveConfig').dispatch('click');
  await ui.change('roleSelect', 'image');
  ui.state.pauseSave.resolve();
  await saving;
  assert.equal(ui.element('settingsResult').textContent, '');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'saved-replacement');
});

test('an earlier save response cannot restore an obsolete configuration', async () => {
  const ui = await page();
  await ui.change('modelName', 'older-model', 'input');
  const firstResponse = deferred();
  ui.state.pauseSave = firstResponse;
  const first = ui.element('saveConfig').dispatch('click');
  await ui.change('modelName', 'latest-model', 'input');
  ui.state.pauseSave = null;
  await ui.element('saveConfig').dispatch('click');
  firstResponse.resolve();
  await first;
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'latest-model');
});

test('editing a model while testing preserves the draft and ignores old result', async () => {
  const ui = await page();
  const pending = ui.begin();
  await until(() => ui.state.tests.length === 1);
  await ui.change('modelName', 'unsaved-model', 'input');
  ui.complete(0);
  await pending;
  assert.equal(ui.element('modelName').value, 'unsaved-model');
  assert.doesNotMatch(ui.element('settingsResult').textContent, /测试成功/);
});

test('a later test retains its result when an earlier failure arrives', async () => {
  const ui = await page();
  const first = ui.begin();
  await until(() => ui.state.tests.length === 1);
  const second = ui.begin();
  await until(() => ui.state.tests.length === 2);
  ui.complete(1);
  await second;
  ui.complete(0, false);
  await first;
  assert.equal(ui.element('settingsResult').textContent, '测试成功：actual-vision');
});

for (const oldSuccess of [true, false]) {
  test(`stale ${oldSuccess ? 'success' : 'failure'} refresh cannot undo a delayed save`, async () => {
    const ui = await page();
    const oldTest = ui.begin();
    await until(() => ui.state.tests.length === 1);
    await ui.change('modelName', 'new-saved-model', 'input');
    const processing = deferred();
    ui.state.pauseSaveProcessing = processing;
    const saving = ui.element('saveConfig').dispatch('click');
    await until(() => ui.state.savesStarted === 2);
    ui.state.pauseReadResponses = true;
    let oldFinished = false;
    const oldCompletion = oldTest.then(() => {
      oldFinished = true;
    });
    ui.complete(0, oldSuccess);
    await until(() => oldFinished || ui.state.reads.length === 1);
    const oldRead = ui.state.reads[0];
    ui.state.pauseReadResponses = false;
    processing.resolve();
    await saving;
    const message = ui.element('settingsResult').textContent;
    if (oldRead) {
      oldRead.resolve(oldRead.snapshot);
    }
    await oldCompletion;
    assert.equal(ui.element('settingsResult').textContent, message);
    await ui.change('roleSelect', 'image');
    await ui.change('roleSelect', 'vision');
    assert.equal(ui.element('modelName').value, 'new-saved-model');
    assert.match(ui.element('keyHint').textContent, /尚未通过/);
    await ui.element('saveConfig').dispatch('click');
    assert.equal(ui.state.roles.vision.model, 'new-saved-model');
  });

  test(`late old ${oldSuccess ? 'success' : 'failure'} GET cannot undo a newer test GET`, async () => {
    const ui = await page();
    const first = ui.begin();
    await until(() => ui.state.tests.length === 1);
    await ui.change('modelName', 'new-tested-model', 'input');
    const processing = deferred();
    ui.state.pauseSaveProcessing = processing;
    const second = ui.begin();
    await until(() => ui.state.savesStarted === 2);
    ui.state.pauseReadResponses = true;
    let firstFinished = false;
    const firstCompletion = first.then(() => {
      firstFinished = true;
    });
    ui.complete(0, oldSuccess);
    await until(() => firstFinished || ui.state.reads.length === 1);
    const oldRead = ui.state.reads[0];
    processing.resolve();
    await until(() => ui.state.tests.length === 2);
    const previousReads = ui.state.reads.length;
    ui.complete(1);
    await until(() => ui.state.reads.length === previousReads + 1);
    const newRead = ui.state.reads[previousReads];
    newRead.resolve(newRead.snapshot);
    await second;
    if (oldRead) {
      oldRead.resolve(oldRead.snapshot);
    }
    await firstCompletion;
    assert.equal(ui.element('settingsResult').textContent, '测试成功：actual-vision');
    await ui.change('roleSelect', 'image');
    await ui.change('roleSelect', 'vision');
    assert.equal(ui.element('modelName').value, 'new-tested-model');
    assert.match(ui.element('keyHint').textContent, /测试已通过/);
  });
}

for (const latestSuccess of [true, false]) {
  test(`in-flight GET loses to a newer test ${latestSuccess ? 'success' : 'failure'}`, async () => {
    const ui = await page();
    ui.state.pauseReadResponses = true;
    const first = ui.begin();
    await until(() => ui.state.tests.length === 1);
    ui.complete(0);
    await until(() => ui.state.reads.length === 1);
    const oldRead = ui.state.reads[0];
    await ui.change('modelName', 'latest-tested-model', 'input');
    const second = ui.begin();
    await until(() => ui.state.tests.length === 2);
    ui.complete(1, latestSuccess);
    await until(() => ui.state.reads.length === 2);
    const newRead = ui.state.reads[1];
    newRead.resolve(newRead.snapshot);
    await second;
    const message = ui.element('settingsResult').textContent;
    oldRead.resolve(oldRead.snapshot);
    await first;
    assert.equal(ui.element('settingsResult').textContent, message);
    await ui.change('roleSelect', 'image');
    await ui.change('roleSelect', 'vision');
    assert.equal(ui.element('modelName').value, 'latest-tested-model');
    assert.match(ui.element('keyHint').textContent,
        latestSuccess ? /测试已通过/ : /尚未通过/);
  });
}

test('initial metadata response cannot undo a save made while loading', async () => {
  const ui = await page({pauseInitialization: true});
  const initialRead = ui.state.reads[0];
  await ui.change('providerSelect', 'openai');
  await ui.change('baseUrl', 'https://replacement.test/v1', 'input');
  await ui.change('modelName', 'saved-before-load', 'input');
  await ui.element('saveConfig').dispatch('click');
  initialRead.resolve(initialRead.snapshot);
  await ui.initializing;
  assert.equal(ui.element('modelName').value, 'saved-before-load');
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'saved-before-load');
});

test('initial metadata survives role navigation and fills the current role', async () => {
  const ui = await page({pauseInitialization: true});
  const initialRead = ui.state.reads[0];
  await ui.change('roleSelect', 'image');
  initialRead.resolve(initialRead.snapshot);
  await ui.initializing;
  assert.equal(ui.element('modelName').value, 'image-original');
  assert.equal(ui.element('baseUrl').value, 'https://example.test/v1');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'vision-original');
  await ui.element('saveConfig').dispatch('click');
  assert.equal(ui.state.roles.vision.model, 'vision-original');
});

test('multiple early role changes preserve the only initial metadata snapshot', async () => {
  const ui = await page({pauseInitialization: true});
  const initialRead = ui.state.reads[0];
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  initialRead.resolve(initialRead.snapshot);
  await ui.initializing;
  assert.equal(ui.element('modelName').value, 'vision-original');
  await ui.change('roleSelect', 'image');
  assert.equal(ui.element('modelName').value, 'image-original');
});

test('initial metadata retains saved roles without overwriting an input draft', async () => {
  const ui = await page({pauseInitialization: true});
  const initialRead = ui.state.reads[0];
  await ui.change('roleSelect', 'image');
  await ui.change('modelName', 'unsaved-image-model', 'input');
  await ui.change('baseUrl', 'https://draft.test/v1', 'input');
  await ui.change('apiKey', 'synthetic-input-sentinel', 'input');
  initialRead.resolve(initialRead.snapshot);
  await ui.initializing;
  assert.equal(ui.element('modelName').value, 'unsaved-image-model');
  assert.equal(ui.element('baseUrl').value, 'https://draft.test/v1');
  assert.equal(ui.element('apiKey').value, 'synthetic-input-sentinel');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'vision-original');
  await ui.change('roleSelect', 'image');
  assert.equal(ui.element('modelName').value, 'image-original');
});

test('initial metadata preserves an explicitly selected provider draft', async () => {
  const ui = await page({pauseInitialization: true});
  const initialRead = ui.state.reads[0];
  await ui.change('roleSelect', 'vision');
  await ui.change('providerSelect', 'anthropic');
  const model = ui.element('modelName').value;
  initialRead.resolve(initialRead.snapshot);
  await ui.initializing;
  assert.equal(ui.element('providerSelect').value, 'anthropic');
  assert.equal(ui.element('modelName').value, model);
  await ui.change('roleSelect', 'image');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('providerSelect').value, 'openai');
  assert.equal(ui.element('modelName').value, 'vision-original');
});

test('initial failure after role navigation stays visible on the current role', async () => {
  const ui = await page({pauseInitialization: true});
  await ui.change('roleSelect', 'image');
  ui.state.reads[0].resolve(response({error: 'initial unavailable'}, false));
  await ui.initializing;
  assert.equal(ui.element('roleSelect').value, 'image');
  assert.equal(ui.element('settingsResult').textContent, '无法连接本机服务。');
});

test('initial failure preserves edited inputs while reporting the load error', async () => {
  const ui = await page({pauseInitialization: true});
  await ui.change('roleSelect', 'vision');
  await ui.change('modelName', 'unsaved-draft', 'input');
  await ui.change('apiKey', 'synthetic-input-sentinel', 'input');
  ui.state.reads[0].resolve(response({error: 'initial unavailable'}, false));
  await ui.initializing;
  assert.equal(ui.element('modelName').value, 'unsaved-draft');
  assert.equal(ui.element('apiKey').value, 'synthetic-input-sentinel');
  assert.equal(ui.element('settingsResult').textContent, '无法连接本机服务。');
});

test('initial failure cannot replace a pending newer save message', async () => {
  const ui = await page({pauseInitialization: true});
  await ui.change('providerSelect', 'openai');
  await ui.change('modelName', 'pending-save-model', 'input');
  const processing = deferred();
  ui.state.pauseSaveProcessing = processing;
  const saving = ui.element('saveConfig').dispatch('click');
  ui.state.reads[0].resolve(response({error: 'initial unavailable'}, false));
  await ui.initializing;
  assert.equal(ui.element('settingsResult').textContent, '正在保存…');
  processing.resolve();
  await saving;
  assert.equal(ui.element('modelName').value, 'pending-save-model');
});

test('a save replacing initial metadata fills a navigated role without edits', async () => {
  const ui = await page({pauseInitialization: true});
  await ui.change('providerSelect', 'openai');
  await ui.change('modelName', 'new-vision-model', 'input');
  const processing = deferred();
  ui.state.pauseSaveProcessing = processing;
  const saving = ui.element('saveConfig').dispatch('click');
  await ui.change('roleSelect', 'image');
  ui.state.reads[0].resolve(ui.state.reads[0].snapshot);
  await ui.initializing;
  processing.resolve();
  await saving;
  assert.equal(ui.element('modelName').value, 'image-original');
  await ui.change('roleSelect', 'vision');
  assert.equal(ui.element('modelName').value, 'new-vision-model');
});
