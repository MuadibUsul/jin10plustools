// Daily layout editor. Preview, PDF and page-image ZIP all render the same PPT geometry.
export function createDailyEditor({ api, onGenerate, canGenerate }) {
  const q = (id) => document.getElementById(id);
  const view = q('preview-view');
  const info = q('preview-info');
  const canvas = q('editor-canvas');
  const scroll = canvas.parentElement;
  const loading = q('editor-loading');
  const loadingTitle = q('editor-loading-title');
  const loadingDetail = q('editor-loading-detail');
  const loadingElapsed = q('editor-loading-elapsed');
  const loadingRetry = q('editor-loading-retry');
  const progress = loading.querySelector('.layout-editor-progress');
  const progressFill = progress.querySelector('span');
  const thumbs = q('editor-thumbs');
  const fieldIds = ['editor-x', 'editor-y', 'editor-width', 'editor-height', 'editor-font'];
  const fields = Object.fromEntries(fieldIds.map((id) => [id, q(id)]));
  let source = null;
  let pages = [];
  let elements = [];
  let pageNodes = [];
  let originals = new Map();
  let overrides = new Map();
  let undo = [];
  let redo = [];
  let pageIndex = 0;
  let selected = null;
  let busy = false;
  let saving = false;
  let ready = false;
  let textFieldSignature = '';
  let requestNumber = 0;
  let imageLoadNumber = 0;
  let loadingTimer = null;
  let previousOverflow = '';

  const key = (item) => `${item.page}:${item.shape_id}`;
  const round = (value) => Math.round(value * 1000000) / 1000000;
  const snapshot = () => JSON.stringify([...overrides]);
  const restore = (saved) => { overrides = new Map(JSON.parse(saved)); };
  const request = () => ({ ...source, layout_overrides: [...overrides.values()].sort((a, b) => a.page - b.page || a.shape_id - b.shape_id) });
  const current = () => (elements[pageIndex] || []).find((item) => key(item) === selected);
  const setStatus = (message) => { info.textContent = message; };

  function stopLoading() {
    clearInterval(loadingTimer);
    loadingTimer = null;
    loading.hidden = true;
  }

  function startLoading(title, detail) {
    clearInterval(loadingTimer);
    loading.classList.remove('error', 'determinate');
    progress.removeAttribute('aria-valuenow');
    progressFill.style.width = '';
    loadingTitle.textContent = title;
    loadingDetail.textContent = detail;
    loadingElapsed.textContent = '已等待 0 秒';
    loadingRetry.hidden = true;
    loading.hidden = false;
    const started = Date.now();
    loadingTimer = setInterval(() => {
      loadingElapsed.textContent = `已等待 ${Math.floor((Date.now() - started) / 1000)} 秒`;
    }, 1000);
  }

  function setLoadingProgress(done, total) {
    const percent = Math.round(done / total * 100);
    loading.classList.add('determinate');
    progress.setAttribute('aria-valuenow', String(percent));
    progress.setAttribute('aria-valuemin', '0');
    progress.setAttribute('aria-valuemax', '100');
    progressFill.style.width = `${percent}%`;
    loadingDetail.textContent = `已保存 ${done} / ${total} 页 · ${percent}%`;
  }

  function showLoadingError(message) {
    clearInterval(loadingTimer);
    loadingTimer = null;
    loading.classList.add('error');
    loadingTitle.textContent = '排版预览加载失败';
    loadingDetail.textContent = message;
    loadingRetry.hidden = false;
    loading.hidden = false;
  }

  function setBusy(value) {
    busy = value;
    q('preview-refresh').disabled = value;
    q('preview-save-images').disabled = value || !ready;
    q('preview-generate').disabled = value || !ready || !canGenerate();
    q('preview-close').disabled = saving;
    for (const input of q('editor-text-fields').querySelectorAll('textarea')) input.disabled = value;
    const item = current();
    for (const [id, input] of Object.entries(fields)) input.disabled = value || !item || (id === 'editor-font' && item.kind !== 'text');
    updateActions();
  }

  function updateActions() {
    q('editor-undo').disabled = busy || undo.length === 0;
    q('editor-redo').disabled = busy || redo.length === 0;
    q('editor-reset-page').disabled = busy || ![...overrides.values()].some((item) => item.page === pageIndex + 1);
    q('editor-reset-element').disabled = busy || !selected || !overrides.has(selected);
  }

  function showInspector(item) {
    q('editor-element-label').textContent = item ? item.label : '未选择元素';
    const values = item ? [item.x, item.y, item.width, item.height].map((value) => (value * 100).toFixed(1)) : ['', '', '', ''];
    ['editor-x', 'editor-y', 'editor-width', 'editor-height'].forEach((id, index) => {
      fields[id].value = values[index];
      fields[id].disabled = !item || busy;
    });
    fields['editor-font'].value = item?.font_size_pt ?? '';
    fields['editor-font'].disabled = !item || item.kind !== 'text' || busy;
    q('editor-font-row').hidden = !item || item.kind !== 'text';
    const signature = item?.kind === 'text' ? `${key(item)}:${JSON.stringify(item.paragraphs)}` : '';
    if (signature !== textFieldSignature) {
      textFieldSignature = signature;
      const container = q('editor-text-fields');
      container.replaceChildren();
      for (const [index, value] of (item?.paragraphs || []).entries()) {
        const label = document.createElement('label');
        label.textContent = `文字段落 ${index + 1}`;
        const input = document.createElement('textarea');
        input.value = value;
        input.disabled = busy;
        input.addEventListener('change', () => {
          const active = current();
          if (!active || busy) return;
          const changed = { ...active, paragraphs: [...active.paragraphs] };
          changed.paragraphs[index] = input.value;
          commit(active, changed);
        });
        label.append(input);
        container.append(label);
      }
    }
    updateActions();
  }

  function placeBox(box, item) {
    box.style.left = `${item.x * 100}%`;
    box.style.top = `${item.y * 100}%`;
    box.style.width = `${item.width * 100}%`;
    box.style.height = `${item.height * 100}%`;
  }

  function valid(item) {
    return [item.x, item.y, item.width, item.height].every(Number.isFinite)
      && item.x >= 0 && item.y >= 0 && item.width > 0 && item.height > 0
      && item.x + item.width <= 1.000001 && item.y + item.height <= 1.000001
      && (item.font_size_pt == null || (item.font_size_pt >= 8 && item.font_size_pt <= 72));
  }

  function commit(item, changed) {
    if (!valid(changed)) { setStatus('位置或尺寸超出了页面范围，请重新输入。'); showInspector(item); return; }
    const original = originals.get(key(item));
    if (!original) { setStatus('元素来源已变化，请返回选稿后重新进入。'); return; }
    const patch = {
      page: item.page, shape_id: item.shape_id, fingerprint: original.fingerprint,
      x: round(changed.x), y: round(changed.y), width: round(changed.width), height: round(changed.height),
      font_size_pt: (changed.font_size_pt ?? null) === (original.font_size_pt ?? null) ? null : (changed.font_size_pt ?? null),
      paragraphs: JSON.stringify(changed.paragraphs) === JSON.stringify(original.paragraphs) ? null : changed.paragraphs,
    };
    const same = ['x', 'y', 'width', 'height'].every((part) => Math.abs(patch[part] - original[part]) < 0.000002)
      && patch.font_size_pt === null && patch.paragraphs === null;
    const before = snapshot();
    if (same) overrides.delete(key(item)); else overrides.set(key(item), patch);
    if (snapshot() === before) return;
    undo.push(before);
    if (undo.length > 50) undo.shift();
    redo = [];
    updateActions();
    renderPreview();
  }

  function startDrag(event, item, box, pageCanvas, pageImage, pageShapes) {
    if (busy) return;
    event.preventDefault();
    selectItem(item);
    const target = box;
    const resize = event.target.classList.contains('layout-resize');
    const bounds = pageCanvas.getBoundingClientRect();
    const start = { x: event.clientX, y: event.clientY, item: { ...item } };
    let draft = { ...item };
    let mask;
    let ghost;
    const addGhost = () => {
      if (ghost || !pageImage.complete || !pageImage.naturalWidth) return;
      const crop = document.createElement('canvas');
      crop.width = Math.max(1, Math.round(item.width * pageImage.naturalWidth));
      crop.height = Math.max(1, Math.round(item.height * pageImage.naturalHeight));
      crop.getContext('2d').drawImage(pageImage, item.x * pageImage.naturalWidth, item.y * pageImage.naturalHeight,
        item.width * pageImage.naturalWidth, item.height * pageImage.naturalHeight, 0, 0, crop.width, crop.height);
      mask = document.createElement('div');
      mask.className = 'layout-shape-mask';
      placeBox(mask, item);
      ghost = document.createElement('div');
      ghost.className = 'layout-shape-ghost';
      ghost.style.backgroundImage = `url(${crop.toDataURL('image/png')})`;
      placeBox(ghost, item);
      pageShapes.prepend(mask, ghost);
    };
    target.setPointerCapture(event.pointerId);
    const move = (next) => {
      addGhost();
      const dx = (next.clientX - start.x) / bounds.width;
      const dy = (next.clientY - start.y) / bounds.height;
      if (resize) {
        draft.width = Math.min(1 - item.x, Math.max(0.01, item.width + dx));
        draft.height = Math.min(1 - item.y, Math.max(0.01, item.height + dy));
      } else {
        draft.x = Math.min(1 - item.width, Math.max(0, item.x + dx));
        draft.y = Math.min(1 - item.height, Math.max(0, item.y + dy));
      }
      placeBox(target, draft);
      if (ghost) placeBox(ghost, draft);
      showInspector(draft);
    };
    const end = () => {
      target.removeEventListener('pointermove', move);
      target.removeEventListener('pointerup', end);
      target.removeEventListener('pointercancel', end);
      if (['x', 'y', 'width', 'height'].some((part) => Math.abs(draft[part] - item[part]) > 0.0001)) commit(item, draft);
      else { mask?.remove(); ghost?.remove(); showInspector(item); }
    };
    target.addEventListener('pointermove', move);
    target.addEventListener('pointerup', end);
    target.addEventListener('pointercancel', end);
  }

  function markCurrentPage() {
    for (const button of thumbs.children) button.classList.toggle('active', Number(button.dataset.page) === pageIndex);
  }

  function selectItem(item) {
    pageIndex = item.page - 1;
    selected = key(item);
    markCurrentPage();
    for (const candidate of canvas.querySelectorAll('.layout-shape')) {
      candidate.classList.toggle('selected', candidate.dataset.shapeKey === selected);
    }
    showInspector(item);
  }

  function syncPageFromScroll() {
    if (!pageNodes.length || view.hidden) return;
    const threshold = scroll.getBoundingClientRect().top + Math.min(scroll.clientHeight * 0.35, 320);
    let index = 0;
    for (const [number, page] of pageNodes.entries()) {
      if (page.getBoundingClientRect().top <= threshold) index = number;
      else break;
    }
    if (index === pageIndex) return;
    pageIndex = index;
    if (selected && !selected.startsWith(`${index + 1}:`)) selected = null;
    markCurrentPage();
    showInspector(current());
  }

  async function showPages() {
    if (!pages.length) throw new Error('没有可显示的页面，请重新渲染。');
    const load = ++imageLoadNumber;
    const scrollTop = scroll.scrollTop;
    let pictures;
    try {
      pictures = await Promise.all(pages.map(async (src, index) => {
        const picture = new Image();
        picture.alt = `第 ${index + 1} 页`;
        picture.draggable = false;
        picture.src = src;
        await picture.decode();
        return picture;
      }));
    } catch { throw new Error('页面图片无法显示，请重新渲染。'); }
    if (load !== imageLoadNumber || view.hidden) return false;
    const nodes = pictures.map((picture, index) => {
      const page = document.createElement('div');
      page.className = 'layout-editor-page';
      page.dataset.page = index;
      const pageShapes = document.createElement('div');
      pageShapes.className = 'layout-editor-shapes';
      page.append(picture, pageShapes);
      for (const item of elements[index] || []) {
        const box = document.createElement('div');
        box.className = `layout-shape${key(item) === selected ? ' selected' : ''}`;
        box.dataset.shapeKey = key(item);
        box.setAttribute('role', 'button');
        box.setAttribute('tabindex', '0');
        box.setAttribute('aria-label', `调整 ${item.label}`);
        const label = document.createElement('span');
        label.className = 'layout-shape-label';
        label.textContent = item.label;
        const handle = document.createElement('span');
        handle.className = 'layout-resize';
        box.append(label, handle);
        placeBox(box, item);
        box.addEventListener('pointerdown', (event) => startDrag(event, item, box, page, picture, pageShapes));
        box.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectItem(item); }
        });
        pageShapes.append(box);
      }
      return page;
    });
    canvas.replaceChildren(...nodes);
    pageNodes = nodes;
    canvas.hidden = false;
    scroll.scrollTop = scrollTop;
    markCurrentPage();
    showInspector(current());
    return true;
  }

  function selectPage(index) {
    if (busy || !pageNodes[index]) return;
    const page = pageNodes[index];
    const top = scroll.scrollTop + page.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 22;
    scroll.scrollTo({ top, behavior: 'smooth' });
  }

  function showThumbs() {
    thumbs.replaceChildren(...pages.map((src, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'layout-page-thumb';
      button.dataset.page = index;
      const picture = document.createElement('img');
      picture.src = src;
      picture.alt = '';
      const number = document.createElement('span');
      number.textContent = `第 ${index + 1} 页`;
      button.append(picture, number);
      button.addEventListener('click', () => selectPage(index));
      return button;
    }));
  }

  async function renderPreview() {
    if (!source || view.hidden) return;
    const call = ++requestNumber;
    ready = false;
    setBusy(true);
    setStatus('正在重新渲染最终版面…');
    startLoading('正在生成日报预览', '正在渲染页面，请稍候…');
    try {
      const result = await api('/api/issues/preview', { method: 'POST', json: request() });
      if (call !== requestNumber || view.hidden) return;
      pages = result.pages;
      elements = result.elements;
      if (!originals.size) for (const group of elements) for (const item of group) originals.set(key(item), { ...item });
      pageIndex = Math.min(pageIndex, pages.length - 1);
      showThumbs();
      if (!await showPages() || call !== requestNumber || view.hidden) return;
      ready = true;
      stopLoading();
      setStatus(`共 ${result.page_count} 页 · 拖动元素调整，松开后预览会更新${result.warnings.length ? ' · ' + result.warnings.join('；') : ''}`);
    } catch (error) {
      if (call === requestNumber && !view.hidden) {
        setStatus(`排版渲染失败：${error.message}`);
        showLoadingError(error.message);
      }
    } finally {
      if (call === requestNumber) { setBusy(false); showInspector(current()); }
    }
  }

  function open(baseRequest) {
    source = structuredClone(baseRequest);
    overrides = new Map();
    originals = new Map();
    undo = [];
    redo = [];
    pages = [];
    elements = [];
    pageNodes = [];
    ready = false;
    pageIndex = 0;
    selected = null;
    textFieldSignature = '';
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    view.hidden = false;
    canvas.hidden = true;
    canvas.replaceChildren();
    scroll.scrollTop = 0;
    thumbs.replaceChildren();
    showInspector(null);
    renderPreview();
  }

  function close() {
    if (saving) return;
    requestNumber += 1;
    imageLoadNumber += 1;
    stopLoading();
    view.hidden = true;
    document.body.style.overflow = previousOverflow;
    source = null;
  }

  function timeTravel(from, to) {
    if (busy || !from.length) return;
    to.push(snapshot());
    restore(from.pop());
    renderPreview();
  }

  function readPageImages(buffer) {
    const view = new DataView(buffer);
    const files = [];
    let offset = 0;
    while (offset + 4 <= buffer.byteLength && view.getUint32(offset, true) === 0x04034b50) {
      if (offset + 30 > buffer.byteLength) throw new Error('图片数据不完整，请重试。');
      const flags = view.getUint16(offset + 6, true);
      const method = view.getUint16(offset + 8, true);
      const size = view.getUint32(offset + 18, true);
      const nameLength = view.getUint16(offset + 26, true);
      const extraLength = view.getUint16(offset + 28, true);
      const start = offset + 30 + nameLength + extraLength;
      if ((flags & 0x08) || method !== 0 || start + size > buffer.byteLength) throw new Error('图片数据格式不受支持，请重试。');
      const name = new TextDecoder().decode(new Uint8Array(buffer, offset + 30, nameLength));
      if (!/^\d+\.jpg$/.test(name) || size > 1_000_000) throw new Error('图片文件名或大小异常，请重试。');
      files.push({ name, bytes: buffer.slice(start, start + size) });
      offset = start + size;
    }
    files.sort((left, right) => Number(left.name.split('.')[0]) - Number(right.name.split('.')[0]));
    if (!files.length || files.some((file, index) => file.name !== `${index + 1}.jpg`)) {
      throw new Error('图片页码不完整，请重试。');
    }
    return files;
  }

  async function createDailyFolder(parent, date) {
    const base = `PLUS日报-${date}`;
    for (let suffix = 0; suffix < 100; suffix += 1) {
      const name = suffix ? `${base}-${suffix + 1}` : base;
      try { await parent.getDirectoryHandle(name); }
      catch (error) {
        if (error.name === 'TypeMismatchError') continue;
        if (error.name !== 'NotFoundError') throw error;
        return { folder: await parent.getDirectoryHandle(name, { create: true }), name };
      }
    }
    throw new Error('目标文件夹中同名日报过多，请选择其他位置。');
  }

  async function saveImages() {
    if (busy || !source || !ready) return;
    if (!window.showDirectoryPicker) {
      setStatus('当前浏览器不支持直接保存到文件夹，请使用最新版 Chrome 或 Edge。');
      return;
    }
    try {
      // The folder picker needs the original click's user activation.
      const parent = await window.showDirectoryPicker({ mode: 'readwrite' });
      const payload = request();
      saving = true;
      setBusy(true);
      startLoading('正在保存所有 JPG 图片', '正在按当前版面生成清晰图片…');
      setStatus('正在生成逐页图片并保存到所选文件夹…');
      const response = await api('/api/issues/preview/images', { method: 'POST', json: payload, raw: true });
      const files = readPageImages(await response.arrayBuffer());
      const { folder, name } = await createDailyFolder(parent, source.issue_date);
      setLoadingProgress(0, files.length);
      for (const [index, file] of files.entries()) {
        const target = await folder.getFileHandle(file.name, { create: true });
        const writer = await target.createWritable();
        try { await writer.write(file.bytes); await writer.close(); }
        catch (error) { try { await writer.abort(); } catch {} throw error; }
        setLoadingProgress(index + 1, files.length);
      }
      setStatus(`已保存 ${files.length} 张 JPG 图片到 ${name} 文件夹，按 1.jpg、2.jpg……命名。`);
    } catch (error) {
      if (error.name !== 'AbortError') setStatus(`保存图片失败：${error.message}`);
    } finally { saving = false; stopLoading(); setBusy(false); }
  }

  q('preview-refresh').addEventListener('click', renderPreview);
  loadingRetry.addEventListener('click', renderPreview);
  q('editor-zoom').addEventListener('input', (event) => {
    const size = `${720 * Number(event.target.value) / 100}px`;
    canvas.style.setProperty('--editor-page-width', size);
    q('editor-zoom-value').textContent = `${event.target.value}%`;
  });
  scroll.addEventListener('scroll', syncPageFromScroll, { passive: true });
  q('preview-save-images').addEventListener('click', saveImages);
  q('preview-close').addEventListener('click', close);
  q('preview-generate').addEventListener('click', () => {
    if (busy || !source || !canGenerate()) return;
    const payload = request();
    close();
    onGenerate(payload);
  });
  q('editor-undo').addEventListener('click', () => timeTravel(undo, redo));
  q('editor-redo').addEventListener('click', () => timeTravel(redo, undo));
  q('editor-reset-page').addEventListener('click', () => {
    if (busy) return;
    const before = snapshot();
    for (const [id, item] of overrides) if (item.page === pageIndex + 1) overrides.delete(id);
    if (snapshot() !== before) { undo.push(before); redo = []; renderPreview(); }
  });
  q('editor-reset-element').addEventListener('click', () => {
    if (busy || !selected || !overrides.has(selected)) return;
    undo.push(snapshot());
    overrides.delete(selected);
    redo = [];
    renderPreview();
  });
  for (const id of fieldIds) fields[id].addEventListener('change', () => {
    const item = current();
    if (!item || busy) return;
    if (['editor-x', 'editor-y', 'editor-width', 'editor-height'].some((name) => fields[name].value.trim() === '')) {
      setStatus('位置和尺寸不能留空。');
      showInspector(item);
      return;
    }
    const changed = {
      ...item,
      x: Number(fields['editor-x'].value) / 100,
      y: Number(fields['editor-y'].value) / 100,
      width: Number(fields['editor-width'].value) / 100,
      height: Number(fields['editor-height'].value) / 100,
      font_size_pt: item.kind === 'text' && fields['editor-font'].value ? Number(fields['editor-font'].value) : null,
    };
    commit(item, changed);
  });
  document.addEventListener('keydown', (event) => {
    if (view.hidden) return;
    const input = /^(INPUT|TEXTAREA)$/.test(event.target.tagName) || event.target.isContentEditable;
    if (event.key === 'Escape') { if (!input) close(); return; }
    const shortcut = (event.ctrlKey || event.metaKey) && ['z', 'y'].includes(event.key.toLowerCase());
    if (shortcut) {
      // Keep native text undo while typing paragraphs; numeric inspector edits are layout actions.
      if (input && event.target.type !== 'number') return;
      event.preventDefault();
      const backward = event.key.toLowerCase() === 'z' && !event.shiftKey;
      timeTravel(backward ? undo : redo, backward ? redo : undo);
      return;
    }
    if (input) return;
    const item = current();
    if (!item || busy || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const delta = event.shiftKey ? 0.01 : 0.002;
    const changed = { ...item };
    if (event.key === 'ArrowLeft') changed.x = Math.max(0, item.x - delta);
    if (event.key === 'ArrowRight') changed.x = Math.min(1 - item.width, item.x + delta);
    if (event.key === 'ArrowUp') changed.y = Math.max(0, item.y - delta);
    if (event.key === 'ArrowDown') changed.y = Math.min(1 - item.height, item.y + delta);
    commit(item, changed);
  });

  return { open, close, request };
}
