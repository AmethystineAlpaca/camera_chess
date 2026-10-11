/* Removable perspective experiment: settings + paired preview only. */
(() => {
  const get = id => document.getElementById(id);
  let enabled = true, saving = false, busy = false, generation = 0, displayed = null;
  const checkbox = get('rectify-board');
  const views = get('camera-views');
  const feedback = get('perspective-feedback');
  const status = get('perspective-status');
  const original = get('camera');
  const corrected = get('rectified-camera');
  const clear = canvas => canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  function apply(value) {
    if (enabled !== value) { generation++; displayed = null; clear(corrected); }
    enabled = value;
    checkbox.checked = value;
    views.classList.toggle('dual-view', value);
    get('rectified-view').hidden = !value;
    get('original-caption').hidden = !value;
    status.hidden = !value;
    if (!value) status.textContent = '';
  }
  function settings(data) {
    if (!saving && typeof data.rectify_board === 'boolean') apply(data.rectify_board);
  }
  async function json(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(url, {...options, cache: 'no-store', signal: controller.signal});
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw Error(error.error || 'Could not load perspective preview.');
      }
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  checkbox.addEventListener('change', async () => {
    const value = checkbox.checked;
    saving = true; checkbox.disabled = true; feedback.textContent = 'Saving…';
    try {
      const data = await json('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rectify_board: value})});
      apply(data.rectify_board);
      feedback.textContent = value ? 'Saved. The next camera frame will use correction when the board can be located.' : 'Saved. Using the original camera view.';
    } catch (error) {
      checkbox.checked = enabled;
      feedback.textContent = error.message || 'Could not save perspective setting.';
    } finally { saving = false; checkbox.disabled = false; }
  });
  function loadImage(base64) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(Error('Could not decode camera preview.'));
      image.src = 'data:image/jpeg;base64,' + base64;
    });
  }
  function draw(canvas, image) {
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    canvas.getContext('2d').drawImage(image, 0, 0);
  }
  async function update(frame) {
    if (busy || displayed === frame.capture_time) return;
    busy = true;
    const revision = generation;
    try {
      if (Date.now()/1000 - frame.capture_time > 3) throw Error('Camera paused or unavailable. Waiting for a fresh frame.');
      const packet = await json('perspective-preview.json?v=' + frame.capture_time);
      if (revision !== generation || !enabled) return;
      if (packet.capture_time < frame.capture_time || !packet.rectification?.enabled) throw Error('Waiting for the next corrected frame…');
      const images = await Promise.all([loadImage(packet.original), loadImage(packet.model_input)]);
      if (revision !== generation || !enabled) return;
      draw(original, images[0]); draw(corrected, images[1]);
      displayed = packet.capture_time;
      status.textContent = packet.rectification.detail;
      get('rectified-caption').textContent = packet.rectification.applied ? 'Corrected · model input' : 'Model input · original fallback';
      corrected.setAttribute('aria-label', packet.rectification.applied ? 'Perspective-corrected model input' : 'Uncorrected model input; board not located');
      status.dataset.state = packet.rectification.state;
    } catch (error) {
      if (revision === generation && enabled) {
        clear(corrected); displayed = null;
        status.textContent = error.message || 'Preview unavailable. Retrying…';
        get('rectified-caption').textContent = 'Model input · waiting';
      }
    } finally { busy = false; }
  }
  window.BoardPerspective = {
    settings,
    get enabled() { return enabled; },
    frame(frame) { if (!enabled) return false; update(frame); return true; },
    stop() { generation++; displayed = null; clear(corrected); status.textContent = 'Waiting for camera…'; },
  };
  apply(true);
})();
