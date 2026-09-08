let configTemplates = {};
let activeTab = 'antigravity';
let currentMode = window.INITIAL_WORKFLOW_MODE || 'architect';
let currentBlueprint = '';

async function loadConfigs() {
    try {
        const resp = await fetch('/api/config-templates');
        configTemplates = await resp.json();
        renderTab('antigravity');
    } catch(e) {}
}
loadConfigs();

function openModal() {
    document.getElementById('configModal').style.display = 'flex';
}
function closeModal() {
    document.getElementById('configModal').style.display = 'none';
}
function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1));
    if (btn) btn.classList.add('active');
    renderTab(tab);
}
function renderTab(tab) {
    const el = document.getElementById('codeSnippet');
    if (!configTemplates[tab]) return;
    if (typeof configTemplates[tab] === 'string') {
        el.innerText = configTemplates[tab];
    } else if (configTemplates[tab].content) {
        el.innerText = JSON.stringify(configTemplates[tab].content, null, 2);
    } else if (configTemplates[tab].snippet) {
        el.innerText = JSON.stringify(configTemplates[tab].snippet, null, 2);
    } else {
        el.innerText = JSON.stringify(configTemplates[tab], null, 2);
    }
}
function copySnippet() {
    const text = document.getElementById('codeSnippet').innerText;
    navigator.clipboard.writeText(text);
    alert('Copied configuration to clipboard!');
}
async function unloadModels() {
    const btn = document.getElementById('btnVramUnload');
    const origText = btn.innerHTML;
    btn.innerHTML = '⏳ Freeing VRAM...';
    btn.disabled = true;
    try {
        const res = await fetch('/api/models/unload', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
            btn.innerHTML = '✅ VRAM Freed!';
            btn.style.background = '#10b981';
            setTimeout(() => {
                location.reload();
            }, 1200);
        } else {
            alert('Notice: ' + (data.error || 'Failed to unload'));
            btn.innerHTML = origText;
            btn.disabled = false;
        }
    } catch (e) {
        alert('Error connecting to gateway: ' + e);
        btn.innerHTML = origText;
        btn.disabled = false;
    }
}
async function preloadModels() {
    const btn = document.getElementById('btnVramPreload');
    const origText = btn.innerHTML;
    btn.innerHTML = '⏳ Loading Weights...';
    btn.disabled = true;
    try {
        const res = await fetch('/api/models/preload', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
            btn.innerHTML = '✅ GPU Warmed!';
            btn.style.background = '#10b981';
            setTimeout(() => {
                location.reload();
            }, 1200);
        } else {
            alert('Notice: ' + (data.error || 'Failed to preload'));
            btn.innerHTML = origText;
            btn.disabled = false;
        }
    } catch (e) {
        alert('Error connecting to gateway: ' + e);
        btn.innerHTML = origText;
        btn.disabled = false;
    }
}
async function autoConfigIDEs() {
    try {
        const res = await fetch('/api/ide/auto-config', { method: 'POST' });
        const data = await res.json();
        if (data.continue_configured) {
            alert('✅ Success! Configured Continue (~/.continue/config.json) to connect to CascadeGateway on localhost:8000.');
        } else {
            alert('IDE Status:\n' + data.details.join('\n'));
        }
    } catch (e) {
        alert('Failed to auto-configure IDEs: ' + e);
    }
}
async function setMode(mode) {
    await fetch('/v1/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode: mode})
    });
    location.reload();
}
async function updateSlider(val) {
    await fetch('/v1/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({bias_factor: parseFloat(val)})
    });
    location.reload();
}

async function loadModelSelection() {
    try {
        const resp = await fetch('/api/models/selection');
        const data = await resp.json();
        const archSelect = document.getElementById('selectArchitect');
        const buildSelect = document.getElementById('selectBuilder');

        const models = data.installed_models || [];
        const currentArch = data.selection.architect;
        const currentBuild = data.selection.builder;

        archSelect.innerHTML = `<option value="auto">⚙️ Auto-Detect (${data.resolved.architect})</option>`;
        buildSelect.innerHTML = `<option value="auto">⚙️ Auto-Detect (${data.resolved.builder})</option>`;

        models.forEach(m => {
            const optA = document.createElement('option');
            optA.value = m;
            optA.innerText = m;
            if (m === currentArch) optA.selected = true;
            archSelect.appendChild(optA);

            const optB = document.createElement('option');
            optB.value = m;
            optB.innerText = m;
            if (m === currentBuild) optB.selected = true;
            buildSelect.appendChild(optB);
        });
    } catch(e) {}
}
loadModelSelection();

async function changeModelSelection() {
    const arch = document.getElementById('selectArchitect').value;
    const build = document.getElementById('selectBuilder').value;
    const status = document.getElementById('modelSelectionStatus');
    status.innerText = 'Updating selection...';
    try {
        await fetch('/api/models/selection', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({architect: arch, builder: build})
        });
        status.innerText = '✓ Models saved';
        status.style.color = '#10b981';
    } catch(e) {
        status.innerText = 'Failed to save';
        status.style.color = '#ef4444';
    }
}

async function setWorkflowMode(mode) {
    await fetch('/v1/workflow/mode', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mode: mode})
    });
    location.reload();
}

async function executeCurrentMode() {
    const prompt = document.getElementById('taskPrompt').value;
    if (!prompt) return;

    if (currentMode === 'architect') {
        await draftArchitecture(prompt);
    } else {
        await runSoloQuery(prompt);
    }
}

async function draftArchitecture(prompt) {
    const status = document.getElementById('pipelineStatus');
    const gate = document.getElementById('reviewGateContainer');
    const box = document.getElementById('blueprintBox');
    const meta = document.getElementById('archMeta');
    const btn = document.getElementById('btnMainAction');

    btn.disabled = true;
    btn.innerHTML = '⏳ Thinking & Modeling...';
    status.innerText = 'Architect reasoning in progress...';
    status.style.color = '#38bdf8';

    try {
        const res = await fetch('/api/pipeline/architect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({prompt: prompt})
        });
        const data = await res.json();
        if (data.success) {
            currentBlueprint = data.blueprint;
            box.innerText = data.blueprint;
            meta.innerText = `Model: ${data.architect_model} | Latency: ${data.latency_sec}s | Saved: ${data.tokens_saved} tokens`;
            gate.style.display = 'block';
            status.innerText = '🛑 Awaiting Human Review Gate Approval';
            status.style.color = '#f59e0b';
            btn.disabled = false;
            btn.innerHTML = '🧠 Draft Blueprint';
        } else {
            alert('Architect failed: ' + (data.detail || 'Unknown error'));
            btn.disabled = false;
            btn.innerHTML = '🧠 Draft Blueprint';
        }
    } catch(e) {
        alert('Error calling Architect: ' + e);
        btn.disabled = false;
        btn.innerHTML = '🧠 Draft Blueprint';
    }
}

async function refineBlueprint() {
    const prompt = document.getElementById('taskPrompt').value;
    const feedback = document.getElementById('reviewFeedback').value;
    if (!feedback) { alert('Please enter feedback to refine the blueprint.'); return; }

    const box = document.getElementById('blueprintBox');
    const meta = document.getElementById('archMeta');
    const status = document.getElementById('pipelineStatus');

    status.innerText = 'Refining blueprint with your feedback...';
    status.style.color = '#38bdf8';
    box.innerText = 'Updating architecture...';

    try {
        const res = await fetch('/api/pipeline/refine', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                prompt: prompt,
                current_blueprint: currentBlueprint,
                feedback: feedback
            })
        });
        const data = await res.json();
        if (data.success) {
            currentBlueprint = data.blueprint;
            box.innerText = data.blueprint;
            meta.innerText = `Refined by ${data.architect_model} | Latency: ${data.latency_sec}s`;
            status.innerText = '🛑 Updated Blueprint Ready for Review';
            status.style.color = '#f59e0b';
            document.getElementById('reviewFeedback').value = '';
        } else {
            alert('Refinement failed: ' + data.detail);
        }
    } catch(e) {
        alert('Error: ' + e);
    }
}

async function approveAndBuild() {
    const prompt = document.getElementById('taskPrompt').value;
    const feedback = document.getElementById('reviewFeedback').value;
    const status = document.getElementById('pipelineStatus');
    const outContainer = document.getElementById('codeOutputContainer');
    const codeResult = document.getElementById('codeResult');
    const buildMeta = document.getElementById('buildMeta');

    status.innerText = '⚡ Builder synthesizing code on local RTX 5090...';
    status.style.color = '#10b981';
    outContainer.style.display = 'block';
    codeResult.innerText = 'Writing implementation adhering to approved blueprint...';

    try {
        const res = await fetch('/api/pipeline/build', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                prompt: prompt,
                approved_blueprint: currentBlueprint,
                feedback: feedback
            })
        });
        const data = await res.json();
        if (data.success) {
            codeResult.innerText = data.code;
            buildMeta.innerText = `Synthesized by ${data.builder_model} in ${data.latency_sec}s (+${data.tokens_saved} tokens saved)`;
            status.innerText = '✅ Implementation Complete!';
            status.style.color = '#10b981';
        } else {
            alert('Build failed: ' + data.detail);
        }
    } catch(e) {
        alert('Error: ' + e);
    }
}

function copyCode() {
    const code = document.getElementById('codeResult').innerText;
    navigator.clipboard.writeText(code);
    alert('Code copied to clipboard!');
}

async function runSoloQuery(prompt) {
    const outContainer = document.getElementById('codeOutputContainer');
    const codeResult = document.getElementById('codeResult');
    const buildMeta = document.getElementById('buildMeta');
    const status = document.getElementById('pipelineStatus');

    status.innerText = 'Routing and executing...';
    outContainer.style.display = 'block';
    codeResult.innerText = 'Processing...';

    const t0 = performance.now();
    const resp = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            model: 'cascade-auto',
            messages: [{role: 'user', content: prompt}]
        })
    });
    const data = await resp.json();
    const dur = ((performance.now() - t0)/1000).toFixed(2);
    const info = data._routing_info || {};
    buildMeta.innerText = `Routed to: ${info.tier || 'Local'} | Latency: ${dur}s | Tokens Saved: ${info.tokens_saved || 0}`;
    codeResult.innerText = data.choices[0].message.content;
    status.innerText = 'Complete';
}
