"""
Popup JS Tests
===============

Behavior tests for popup.js DOM update logic, executed with Node.js
against a minimal DOM stub.  Skipped when Node.js is not installed -
the app itself never needs Node; it is only used as a test runner
for the popup's JavaScript.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_POPUP_JS = Path(__file__).parent.parent / 'usage_monitor_for_claude' / 'popup' / 'popup.js'

_NODE = shutil.which('node')

# Minimal DOM stub covering exactly the APIs the bar create/update path uses.
_DOM_STUB = r'''
class StubElement {
    constructor(tag) {
        this.tagName = tag;
        this.className = '';
        this.textContent = '';
        this.title = '';
        this.listeners = {};
        this.disabled = false;
        this.style = {};
        this.dataset = {};
        this.children = [];
        this.parentNode = null;
        const element = this;
        this.classList = {
            toggle(name, force) {
                const classes = element._classSet();
                const on = force === undefined ? !classes.has(name) : !!force;
                if (on) classes.add(name); else classes.delete(name);
                element.className = [...classes].join(' ');
                return on;
            },
            add(name) { const classes = element._classSet(); classes.add(name); element.className = [...classes].join(' '); },
            remove(name) { const classes = element._classSet(); classes.delete(name); element.className = [...classes].join(' '); },
            contains(name) { return element._classSet().has(name); },
        };
    }
    _classSet() { return new Set(this.className.split(/\s+/).filter(Boolean)); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) {
        if (name === 'class') this.className = String(value);
        else (this.attributes ||= {})[name] = String(value);
    }
    getAttribute(name) { return name === 'class' ? this.className : (this.attributes || {})[name]; }
    click() { if (this.listeners.click) this.listeners.click(); }
    appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
    append(...nodes) { for (const node of nodes) this.appendChild(node); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    remove() {
        if (this.parentNode) {
            const index = this.parentNode.children.indexOf(this);
            if (index >= 0) this.parentNode.children.splice(index, 1);
            this.parentNode = null;
        }
    }
    matches(selector) { return selector.startsWith('.') && this._classSet().has(selector.slice(1)); }
    querySelector(selector) {
        for (const child of this.children) {
            if (child.matches(selector)) return child;
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }
        return null;
    }
    querySelectorAll(selector) {
        const found = [];
        for (const child of this.children) {
            if (child.matches(selector)) found.push(child);
            found.push(...child.querySelectorAll(selector));
        }
        return found;
    }
}

globalThis.document = {
    createElement: (tag) => new StubElement(tag),
    createElementNS: (_namespace, tag) => new StubElement(tag),
    getElementById: () => null,
    body: new StubElement('body'),
};
globalThis.ResizeObserver = class { constructor() {} observe() {} };
globalThis.requestAnimationFrame = (callback) => callback();
'''

_SCENARIO_PRELUDE = r'''
els = { usageBars: document.createElement('div') };

function makeEntry(overrides) {
    return Object.assign({
        key: 'five_hour', label: '5h', pct_text: '0%', fill_pct: 0.0,
        warn: false, dividers: [], marker_rel: null, reset_text: '',
    }, overrides);
}

// Full element set for the scenarios that drive updateData() end to end.
function makeEls() {
    const names = [
        'accountSection', 'emailRow', 'emailValue', 'planRow', 'planValue',
        'usageSection', 'headingUsage', 'usageBars',
        'extraSection', 'extraSpent', 'extraPct', 'extraBarContainer', 'extraFill', 'extraBalance',
        'installSection', 'installRows', 'statusSection', 'statusText',
    ];
    return Object.fromEntries(names.map((name) => [name, document.createElement('div')]));
}

function makeChart(overrides) {
    return Object.assign({
        actual: [[0, 0], [0.2, 20], [0.5, 50]], measured_from: 0.2, now_x: 0.5,
        ticks: [{ x: 0.1, label: 'Fri' }, { x: 0.25, label: 'Sat' }],
        projection: [[0.5, 50], [0.75, 75], [1, 100]], runs_out: false, summary: 'At this pace: ~100% at reset',
    }, overrides);
}

function chartPaths(entryElement) {
    return entryElement.querySelector('.forecast-chart').children.filter((child) => child.tagName === 'path')
        .map((child) => ({ class: child.className, d: child.getAttribute('d') }));
}

function makeExtra(overrides) {
    return Object.assign({
        has_limit: true, pct_text: '25%', fill_pct: 0.25, spent_text: '$25.00 / $100.00 spent', balance_text: '',
    }, overrides);
}
'''


def _run_scenario(scenario: str) -> dict:
    """Execute the DOM stub + popup.js + scenario with Node and parse its JSON output."""
    script = _DOM_STUB + _POPUP_JS.read_text(encoding='utf-8') + _SCENARIO_PRELUDE + scenario
    with TemporaryDirectory() as tmp:
        script_path = Path(tmp) / 'scenario.js'
        script_path.write_text(script, encoding='utf-8')
        proc = subprocess.run([_NODE, str(script_path)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f'Node scenario failed:\n{proc.stderr}')
    return json.loads(proc.stdout)


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestUsageBarUpdates(unittest.TestCase):
    """Tests for updateUsageBars/updateBarElement in popup.js."""

    def test_changed_field_set_with_equal_count_updates_labels(self):
        """When the set of quota fields changes but the count stays the same
        (e.g. an account switch between plans), the bars must not show the new
        percentages under the old labels."""
        result = _run_scenario('''
updateUsageBars([
    makeEntry({ key: 'five_hour', label: '5h', pct_text: '10%' }),
    makeEntry({ key: 'seven_day', label: '7d', pct_text: '20%' }),
]);
updateUsageBars([
    makeEntry({ key: 'five_hour', label: '5h', pct_text: '30%' }),
    makeEntry({ key: 'seven_day_opus', label: '7d Opus', pct_text: '99%' }),
]);
console.log(JSON.stringify(els.usageBars.children.map((bar) => ({
    label: bar.children[0].children[0].textContent,
    pct: bar.querySelector('.bar-pct').textContent,
}))));
''')
        self.assertEqual(result, [
            {'label': '5h', 'pct': '30%'},
            {'label': '7d Opus', 'pct': '99%'},
        ])

    def test_marker_and_divider_positions_stable_across_update(self):
        """The 2 px marker/divider elements are centered with a -1px correction
        on create; an in-place update must use the identical expression, or the
        elements shift by 1 px after the first data update."""
        result = _run_scenario('''
const fields = { key: 'five_hour', label: '5h', marker_rel: 0.5, dividers: [0.25] };
updateUsageBars([makeEntry(Object.assign({ pct_text: '10%' }, fields))]);
const container = els.usageBars.children[0].querySelector('.bar-container');
const before = {
    marker: container.querySelector('.bar-marker').style.left,
    divider: container.querySelector('.bar-divider').style.left,
};
updateUsageBars([makeEntry(Object.assign({ pct_text: '11%' }, fields))]);
const after = {
    marker: container.querySelector('.bar-marker').style.left,
    divider: container.querySelector('.bar-divider').style.left,
};
console.log(JSON.stringify({ before, after }));
''')
        self.assertEqual(result['after'], result['before'])

    def test_unchanged_field_set_updates_in_place(self):
        """With an unchanged field set, bars are updated in place (no rebuild)."""
        result = _run_scenario('''
updateUsageBars([makeEntry({ key: 'five_hour', label: '5h', pct_text: '10%' })]);
const barBefore = els.usageBars.children[0];
updateUsageBars([makeEntry({ key: 'five_hour', label: '5h', pct_text: '50%', fill_pct: 0.5 })]);
console.log(JSON.stringify({
    sameElement: els.usageBars.children[0] === barBefore,
    pct: els.usageBars.children[0].querySelector('.bar-pct').textContent,
    fillWidth: els.usageBars.children[0].querySelector('.bar-fill').style.width,
}));
''')
        self.assertEqual(result, {'sameElement': True, 'pct': '50%', 'fillWidth': '50%'})


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestExtraUsageSection(unittest.TestCase):
    """Tests for the prepaid balance line in the extra-usage section."""

    def test_balance_line_shown_when_present(self):
        """A rendered balance is shown below the extra-usage bar."""
        result = _run_scenario('''
els = makeEls();
updateData({ profile: null, usage: [], extra: makeExtra({ balance_text: '$55.97 available' }), installations: [], status: null });
console.log(JSON.stringify({ text: els.extraBalance.textContent, display: els.extraBalance.style.display }));
''')
        self.assertEqual(result, {'text': '$55.97 available', 'display': ''})

    def test_balance_line_hidden_when_absent(self):
        """Without a balance the line is hidden, leaving the section as it was before."""
        result = _run_scenario('''
els = makeEls();
updateData({ profile: null, usage: [], extra: makeExtra(), installations: [], status: null });
const empty = { text: els.extraBalance.textContent, display: els.extraBalance.style.display };
updateData({ profile: null, usage: [], extra: makeExtra({ balance_text: undefined }), installations: [], status: null });
const missing = { text: els.extraBalance.textContent, display: els.extraBalance.style.display };
console.log(JSON.stringify({ empty, missing }));
''')
        self.assertEqual(result, {
            'empty': {'text': '', 'display': 'none'},
            'missing': {'text': '', 'display': 'none'},
        })

    def test_balance_line_cleared_on_update(self):
        """A balance that becomes unavailable is removed instead of lingering."""
        result = _run_scenario('''
els = makeEls();
updateData({ profile: null, usage: [], extra: makeExtra({ balance_text: '$55.97 available' }), installations: [], status: null });
updateData({ profile: null, usage: [], extra: makeExtra(), installations: [], status: null });
console.log(JSON.stringify({ text: els.extraBalance.textContent, display: els.extraBalance.style.display }));
''')
        self.assertEqual(result, {'text': '', 'display': 'none'})


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestForecastChart(unittest.TestCase):
    """Tests for a usage entry drawn as a forecast chart in place of its bar."""

    def test_chart_replaces_bar(self):
        """An entry with a chart has no bar; label, percentage, reset time and summary stay."""
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({
    key: 'seven_day', label: 'Weekly', pct_text: '50%', reset_text: 'Resets Thu',
    chart: makeChart({ runs_out: true, summary: 'At this pace: empty ~Mon' }),
})]);
const entry = els.usageBars.children[0];
console.log(JSON.stringify({
    bar: !!entry.querySelector('.bar-container'),
    pct: entry.querySelector('.bar-pct').textContent,
    reset: entry.querySelector('.reset-text').textContent,
    summary: entry.querySelector('.forecast-summary').textContent,
    paths: chartPaths(entry).map((path) => path.class),
}));
''')
        self.assertEqual(result, {
            'bar': False, 'pct': '50%', 'reset': 'Resets Thu', 'summary': 'At this pace: empty ~Mon',
            'paths': ['assumed', 'actual', 'projection warn'],
        })

    def test_entry_without_chart_keeps_bar(self):
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({ chart: null })]);
const entry = els.usageBars.children[0];
console.log(JSON.stringify({ bar: !!entry.querySelector('.bar-container'), chart: !!entry.querySelector('.forecast-chart') }));
''')
        self.assertEqual(result, {'bar': True, 'chart': False})

    def test_chart_updated_in_place(self):
        """A new snapshot redraws the same element."""
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({ key: 'seven_day', pct_text: '50%', chart: makeChart() })]);
const entry = els.usageBars.children[0];
updateUsageBars([makeEntry({ key: 'seven_day', pct_text: '60%', chart: makeChart({ summary: 'At this pace: empty ~Tue', projection: [] }) })]);
console.log(JSON.stringify({
    same: els.usageBars.children[0] === entry,
    pct: entry.querySelector('.bar-pct').textContent,
    summary: entry.querySelector('.forecast-summary').textContent,
    paths: chartPaths(entry).map((path) => path.class),
}));
''')
        self.assertEqual(result, {'same': True, 'pct': '60%', 'summary': 'At this pace: empty ~Tue', 'paths': ['assumed', 'actual']})

    def test_switching_between_chart_and_bar_rebuilds_entry(self):
        """A quota that loses its reset window goes back to a bar, and a chart returns with it."""
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({ key: 'seven_day', chart: makeChart() })]);
updateUsageBars([makeEntry({ key: 'seven_day', chart: null })]);
const afterBar = !!els.usageBars.children[0].querySelector('.bar-container');
updateUsageBars([makeEntry({ key: 'seven_day', chart: makeChart() })]);
const afterChart = !!els.usageBars.children[0].querySelector('.forecast-chart');
console.log(JSON.stringify({ afterBar, afterChart }));
''')
        self.assertEqual(result, {'afterBar': True, 'afterChart': True})

    def test_hidden_by_field_name_in_compact_view(self):
        """The chart entry is hidden in the pinned view like any bar, by its field name."""
        result = _run_scenario('''
els = makeEls();
translations = { forecast_now: 'Now' };
popupPinned = true;
compactHide = ['seven_day'];
updateData({ profile: null, usage: [makeEntry({ key: 'five_hour' }), makeEntry({ key: 'seven_day', chart: makeChart() })], extra: null, installations: [], status: null });
console.log(JSON.stringify(els.usageBars.children.map((entry) => entry.dataset.key)));
''')
        self.assertEqual(result, ['five_hour'])

    def test_measured_line_starts_at_first_reading(self):
        """The dotted segment covers only the stretch before the first logged reading."""
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({ chart: makeChart() })]);
console.log(JSON.stringify(chartPaths(els.usageBars.children[0]).map((path) => path.d.split(' ').length)));
''')
        self.assertEqual(result, [2, 2, 3])

    def test_without_log_only_dotted_line(self):
        """With no readings yet the whole line to now is the dotted, assumed one."""
        result = _run_scenario('''
translations = { forecast_now: 'Now' };
updateUsageBars([makeEntry({ chart: makeChart({ actual: [[0, 0], [0.5, 50]], measured_from: 0.5 }) })]);
console.log(JSON.stringify(chartPaths(els.usageBars.children[0]).map((path) => path.class)));
''')
        self.assertEqual(result, ['assumed', 'projection'])


_REFRESH_PRELUDE = r'''
const refreshBtn = document.createElement('button');
const refreshControl = document.createElement('span');
refreshControl.appendChild(refreshBtn);
document.getElementById = (id) => ({ refreshBtn, refreshControl }[id] || null);
translations = {
    refresh: 'Refresh', status_refreshing: 'Refreshing...',
    refresh_deferred_reset: 'Refresh available after quota reset ({duration})',
    refresh_cooldown: 'Refresh available in {duration}',
    refresh_backoff: 'Rate limit: retry in {duration}',
    duration_s: '{s}s', duration_m: '{m}m', duration_hm: '{h}h {m}m',
    status_updated_s: 'Updated {s}s ago', status_updated: 'Updated {duration} ago',
    status_next_update: 'Next update in {duration}',
};
let now = 1000;
Date.now = () => now * 1000;
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};
els = makeEls();
setupRefreshButton();
'''


@unittest.skipUnless(_NODE, 'Node.js not available')
class TestManualRefresh(unittest.TestCase):
    """Tests for the popup refresh command and cache-driven wait state."""

    def test_duplicate_click_suppressed_and_result_does_not_replace_snapshot(self):
        """The command result never competes with the pushed snapshot channel."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
let resolveRefresh;
let calls = 0;
globalThis.pywebview = { api: { refresh: () => {
    calls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
} } };
const snapshot = { profile: null, usage: [makeEntry({ pct_text: '42%' })], installations: [], status: null };
updateData(snapshot);
refreshBtn.click();
refreshBtn.click();
const loading = refreshBtn.disabled && refreshBtn.classList.contains('refreshing');
const newer = { ...snapshot, usage: [makeEntry({ pct_text: '43%' })] };
updateData(newer);
resolveRefresh({ status: { text: 'older response' } });
setTimeout(() => console.log(JSON.stringify({
    calls, loading, retainedNewest: lastData === newer,
    pct: els.usageBars.children[0].querySelector('.bar-pct').textContent,
    disabled: refreshBtn.disabled, refreshing: refreshBtn.classList.contains('refreshing'),
    title: refreshControl.title, ariaLabel: refreshBtn.getAttribute('aria-label'),
})), 0);
''')
        self.assertEqual(result, {
            'calls': 1, 'loading': True, 'retainedNewest': True, 'pct': '43%',
            'disabled': False, 'refreshing': False, 'title': 'Refresh', 'ariaLabel': 'Refresh',
        })

    def test_rejection_restores_button_without_replacing_data(self):
        """A rejected bridge call removes only its own pending state."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
let rejectRefresh;
globalThis.pywebview = { api: { refresh: () => new Promise((_resolve, reject) => { rejectRefresh = reject; }) } };
const snapshot = { profile: null, usage: [makeEntry({ pct_text: '42%' })], installations: [], status: null };
updateData(snapshot);
refreshBtn.click();
const loading = refreshBtn.disabled && refreshBtn.classList.contains('refreshing');
rejectRefresh(new Error('offline'));
setTimeout(() => console.log(JSON.stringify({
    loading, retained: lastData === snapshot, disabled: refreshBtn.disabled,
    refreshing: refreshBtn.classList.contains('refreshing'),
})), 0);
''')
        self.assertEqual(result, {'loading': True, 'retained': True, 'disabled': False, 'refreshing': False})

    def test_synchronous_bridge_exception_cleans_loading_state(self):
        """A bridge unavailable before it creates a promise also leaves the button usable."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
globalThis.pywebview = { api: { refresh: () => { throw new Error('bridge unavailable'); } } };
refreshBtn.click();
console.log(JSON.stringify({ disabled: refreshBtn.disabled, refreshing: refreshBtn.classList.contains('refreshing') }));
''')
        self.assertEqual(result, {'disabled': False, 'refreshing': False})

    def test_snapshot_refreshing_prevents_requests_and_survives_promise_completion(self):
        """Tray or automatic loading remains active when the popup command completes."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
let calls = 0;
let resolveRefresh;
globalThis.pywebview = { api: { refresh: () => {
    calls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
} } };
updateStatus({ text: 'Loading', refreshing: true });
refreshBtn.click();
const snapshotBlocked = calls === 0 && refreshBtn.disabled;
updateStatus({ text: 'Ready', refreshing: false });
refreshBtn.click();
updateStatus({ text: 'Loading', refreshing: true });
resolveRefresh();
setTimeout(() => {
    const stillLoading = refreshBtn.disabled && refreshBtn.classList.contains('refreshing');
    updateStatus({ text: 'Ready', refreshing: false });
    console.log(JSON.stringify({ snapshotBlocked, calls, stillLoading, disabled: refreshBtn.disabled }));
}, 0);
''')
        self.assertEqual(result, {'snapshotBlocked': True, 'calls': 1, 'stillLoading': True, 'disabled': False})

    def test_reset_deferral_countdown_without_usage(self):
        """A reset delay is shown in the tooltip and leaves static status alone."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
updateStatus({ text: 'Loading...', refreshing: false, manual_refresh_reason: 'reset', manual_refresh_retry_at: 1005 });
const initial = {
    disabled: refreshBtn.disabled, spinning: refreshBtn.classList.contains('refreshing'),
    text: els.statusText.textContent, title: refreshControl.title,
};
now = 1004;
tickStatusText();
const later = refreshControl.title;
now = 1005;
tickStatusText();
console.log(JSON.stringify({ initial, later, expiredDisabled: refreshBtn.disabled, expiredTitle: refreshControl.title }));
''')
        self.assertEqual(result, {
            'initial': {
                'disabled': True, 'spinning': False,
                'text': 'Loading...', 'title': 'Refresh available after quota reset (5s)',
            },
            'later': 'Refresh available after quota reset (1s)', 'expiredDisabled': False, 'expiredTitle': 'Refresh',
        })

    def test_cooldown_and_backoff_disable_requests_and_keep_cached_usage(self):
        """The tooltip counts down without replacing the regular status or usage."""
        for reason, message in [('cooldown', 'Refresh available in 10s'), ('backoff', 'Rate limit: retry in 10s')]:
            with self.subTest(reason=reason):
                result = _run_scenario(_REFRESH_PRELUDE + f'''
let calls = 0;
globalThis.pywebview = {{ api: {{ refresh: async () => {{ calls += 1; }} }} }};
const snapshot = {{
    profile: null, usage: [makeEntry({{ pct_text: '42%' }})], installations: [],
    status: {{ last_success_time: 990, next_poll_time: 1050, refreshing: false,
        manual_refresh_reason: '{reason}', manual_refresh_retry_at: 1010 }},
}};
updateData(snapshot);
refreshBtn.click();
const blocked = calls === 0 && refreshBtn.disabled;
const title = refreshControl.title;
const text = els.statusText.textContent;
now = 1010;
tickStatusText();
const available = !refreshBtn.disabled;
console.log(JSON.stringify({{
    blocked, title, text, available, retained: lastData === snapshot,
    pct: els.usageBars.children[0].querySelector('.bar-pct').textContent,
}}));
''')
                self.assertEqual(result, {
                    'blocked': True, 'title': message, 'text': 'Updated 10s ago',
                    'available': True, 'retained': True, 'pct': '42%',
                })

    def test_error_status_still_shows_refresh_backoff(self):
        """An empty failed cache keeps its error and shows backoff on hover."""
        result = _run_scenario(_REFRESH_PRELUDE + '''
updateStatus({ text: 'Rate limited', is_error: true, refreshing: false,
    manual_refresh_reason: 'backoff', manual_refresh_retry_at: 1008 });
console.log(JSON.stringify({
    text: els.statusText.textContent, title: els.statusText.title,
    tooltip: refreshControl.title, error: els.statusSection.classList.contains('error'), disabled: refreshBtn.disabled,
}));
''')
        self.assertEqual(result, {
            'text': 'Rate limited', 'title': 'Rate limited',
            'tooltip': 'Rate limit: retry in 8s', 'error': True, 'disabled': True,
        })


if __name__ == '__main__':
    unittest.main()
