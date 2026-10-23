// Daily layout editor. Preview, PDF and page-image ZIP all render the same PPT geometry.
export function createDailyEditor({ api, onGenerate, canGenerate }) {
  const q = (id) => document.getElementById(id);
  const view = q('preview-view');
  const info = q('preview-info');
  const canvas = q('editor-canvas');
  const image = q('editor-image');
  const shapes = q('editor-shapes');
  const thumbs = q('editor-thumbs');
  const fieldIds = ['editor-x', 'editor-y', 'editor-width', 'editor-height', 'editor-font'];
  const fields = Object.fromEntries(fieldIds.map((id) => [id, q(id)]));
  let source = null;
  let pages = [];
  let elements = [];
  let originals = new Map();
  let overrides = new Map();
  let undo = [];
  let redo = [];
  let pageIndex = 0;
  let selected = null;
  let busy = false;
  let ready = false;
  let textFieldSignature = '';
  let requestNumber = 0;
  let previousOverflow = '';

  const key = (item) => `${item.page}:${item.shape_id}`;
  const round = (value) => Math.round(value * 1000000) / 1000000;
  const snapshot = () => JSON.stringify([...overrides]);
  const restore = (saved) => { overrides = new Map(JSON.parse(saved)); };
  const request = () => ({ ...source, layout_overrides: [...overrides.values()].sort((a, b) => a.page - b.page || a.shape_id - b.shape_id) });
  const current = () => (elements[pageIndex] || []).find((item) => key(item) === selected);
  const setStatus = (message) => { info.textContent = message; };

  function setBusy(value) {
    busy = value;
    q('preview-refresh').disabled = value;
    q('preview-save-images').disabled = value || !ready;
    q('preview-generate').disabled = value || !ready || !canGenerate();
    for (const input of q('editor-text-fields').querySelectorAll('textarea')) input.disabled = value;
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

  function startDrag(event, item, box) {
    if (busy) return;
    event.preventDefault();
    selected = key(item);
    for (const candidate of shapes.children) candidate.classList.toggle('selected', candidate.dataset.shapeKey === selected);
    showInspector(item);
    const target = box;
    const resize = event.target.classList.contains('layout-resize');
    const bounds = canvas.getBoundingClientRect();
    const start = { x: event.clientX, y: event.clientY, item: { ...item } };
    let draft = { ...item };
    let mask;
    let ghost;
    const addGhost = () => {
      if (ghost || !image.complete || !image.naturalWidth) return;
      const crop = document.createElement('canvas');
      crop.width = Math.max(1, Math.round(item.width * image.naturalWidth));
      crop.height = Math.max(1, Math.round(item.height * image.naturalHeight));
      crop.getContext('2d').drawImage(image, item.x * image.naturalWidth, item.y * image.naturalHeight,
        item.width * image.naturalWidth, item.height * image.naturalHeight, 0, 0, crop.width, crop.height);
      mask = document.createElement('div');
      mask.className = 'layout-shape-mask';
      placeBox(mask, item);
      ghost = document.createElement('div');
      ghost.className = 'layout-shape-ghost';
      ghost.style.backgroundImage = `url(${crop.toDataURL('image/png')})`;
      placeBox(ghost, item);
      shapes.prepend(mask, ghost);
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

  function showPage() {
    if (!pages.length) return;
    image.src = pages[pageIndex];
    image.alt = `第 ${pageIndex + 1} 页`;
    for (const button of thumbs.children) button.classList.toggle('active', Number(button.dataset.page) === pageIndex);
    shapes.replaceChildren();
    for (const item of elements[pageIndex] || []) {
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
      box.addEventListener('pointerdown', (event) => startDrag(event, item, box));
      box.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selected = key(item); showPage(); }
      });
      shapes.append(box);
    }
    showInspector(current());
    updateActions();
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
      button.addEventListener('click', () => { pageIndex = index; selected = null; showPage(); });
      return button;
    }));
  }

  async function renderPreview() {
    if (!source || view.hidden) return;
    const call = ++requestNumber;
    ready = false;
    setBusy(true);
    setStatus('正在重新渲染最终版面…');
    try {
      const result = await api('/api/issues/preview', { method: 'POST', json: request() });
      if (call !== requestNumber || view.hidden) return;
      pages = result.pages;
      elements = result.elements;
      ready = true;
      if (!originals.size) for (const group of elements) for (const item of group) originals.set(key(item), { ...item });
      pageIndex = Math.min(pageIndex, pages.length - 1);
      showThumbs();
      showPage();
      setStatus(`共 ${result.page_count} 页 · 拖动元素调整，松开后预览会更新${result.warnings.length ? ' · ' + result.warnings.join('；') : ''}`);
    } catch (error) {
      if (call === requestNumber) setStatus(`排版渲染失败：${error.message}`);
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
    ready = false;
    pageIndex = 0;
    selected = null;
    textFieldSignature = '';
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    view.hidden = false;
    image.removeAttribute('src');
    thumbs.replaceChildren();
    shapes.replaceChildren();
    showInspector(null);
    renderPreview();
  }

  function close() {
    requestNumber += 1;
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

  async function saveImages() {
    if (busy || !source) return;
    const filename = `PLUS日报-${source.issue_date}-图片.zip`;
    let handle;
    try {
      // Native picker must be called before the first network await.
      if (window.showSaveFilePicker) handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: 'ZIP 压缩包', accept: { 'application/zip': ['.zip'] } }],
      });
      const payload = request();
      setBusy(true);
      setStatus('正在按当前版面生成清晰图片…');
      const response = await api('/api/issues/preview/images', { method: 'POST', json: payload, raw: true });
      const blob = await response.blob();
      if (handle) {
        const writer = await handle.createWritable();
        await writer.write(blob);
        await writer.close();
      } else {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      setStatus(`已保存 ${filename}：压缩包内图片按 1.webp、2.webp……命名`);
    } catch (error) {
      if (error.name !== 'AbortError') setStatus(`保存图片失败：${error.message}`);
    } finally { setBusy(false); }
  }

  q('preview-refresh').addEventListener('click', renderPreview);
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
    if (view.hidden || /^(INPUT|TEXTAREA)$/.test(event.target.tagName)) return;
    if (event.key === 'Escape') { close(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault(); timeTravel(event.shiftKey ? redo : undo, event.shiftKey ? undo : redo); return;
    }
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
