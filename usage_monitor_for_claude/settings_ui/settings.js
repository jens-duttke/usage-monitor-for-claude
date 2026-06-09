'use strict';

function init(config) {
    const t = config.t;
    const d = config.data;

    // i18n strings
    document.getElementById('h-thresholds').textContent   = t.thresholds_section;
    document.getElementById('h-alert1').textContent        = t.alert1;
    document.getElementById('h-alert2').textContent        = t.alert2;
    document.getElementById('l-five-hour').textContent     = t.five_hour;
    document.getElementById('l-seven-day').textContent     = t.seven_day;
    document.getElementById('hint-thresh').textContent     = t.hint_thresh;
    document.getElementById('btn-save').textContent        = t.save;
    document.getElementById('btn-cancel').textContent      = t.cancel;

    // Fill current values (show empty string if no value at that position)
    _setInput('t5-1', d.five_hour[0]);
    _setInput('t5-2', d.five_hour[1]);
    _setInput('t7-1', d.seven_day[0]);
    _setInput('t7-2', d.seven_day[1]);

    // Report height so Python can size the window
    const _ro = new ResizeObserver(() => {
        const h = document.documentElement.scrollHeight;
        window.pywebview.api.report_height(h + 2);
    });
    _ro.observe(document.getElementById('app'));

    document.getElementById('btn-cancel').onclick = () => window.pywebview.api.close();
    document.getElementById('btn-save').onclick = _save;

    // Allow Enter to save
    document.addEventListener('keydown', e => {
        if (e.key === 'Enter') _save();
        if (e.key === 'Escape') window.pywebview.api.close();
    });
}

function _setInput(id, value) {
    const el = document.getElementById(id);
    el.value = (value !== null && value !== undefined) ? String(value) : '';
}

function _parseThresholds(id1, id2) {
    const v1 = parseInt(document.getElementById(id1).value, 10);
    const v2 = parseInt(document.getElementById(id2).value, 10);
    const vals = [];
    if (!isNaN(v1) && v1 >= 1 && v1 <= 99) vals.push(v1);
    if (!isNaN(v2) && v2 >= 1 && v2 <= 99) vals.push(v2);
    // deduplicate and sort
    return [...new Set(vals)].sort((a, b) => a - b);
}

function _save() {
    const five_hour = _parseThresholds('t5-1', 't5-2');
    const seven_day = _parseThresholds('t7-1', 't7-2');

    const errorEl = document.getElementById('error');
    if (five_hour.length === 0 || seven_day.length === 0) {
        errorEl.textContent = document.getElementById('hint-thresh').dataset.errEmpty || 'At least one threshold is required.';
        errorEl.style.display = 'block';
        return;
    }
    errorEl.style.display = 'none';

    window.pywebview.api.save({ five_hour, seven_day });
}
