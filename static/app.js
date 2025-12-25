/**
 * Probate CRM - JavaScript
 */

// State
let allRecords = [];
let filteredRecords = [];
let currentView = 'dashboard';
let currentRecord = null;
let sortColumn = 'date_of_death';
let sortDirection = 'desc';

// Status options
const STATUSES = [
    { value: 'new', label: 'New', color: 'blue' },
    { value: 'contacted', label: 'Contacted', color: 'purple' },
    { value: 'qualified', label: 'Qualified', color: 'cyan' },
    { value: 'converted', label: 'Converted', color: 'green' },
    { value: 'not-interested', label: 'Not Interested', color: 'gray' },
    { value: 'lost', label: 'Lost', color: 'red' },
];

// DOM Elements
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    loadRecords();
    setupEventListeners();
});

// Theme
function initTheme() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

// Data Loading
async function loadRecords() {
    showLoading(true);
    showSkeletonStats(true);
    
    try {
        const response = await fetch('/api/records');
        if (!response.ok) throw new Error('Failed to load');
        
        allRecords = await response.json();
        populateCountyFilter();
        populateRelationshipFilter();
        applyFilters();
        updateStats();
        
    } catch (error) {
        console.error('Error:', error);
    } finally {
        showLoading(false);
        showSkeletonStats(false);
    }
}

// Show skeleton loading state for stats
function showSkeletonStats(show) {
    const statElements = ['#statTotal', '#statNew', '#statContacted', '#statFollowUp'];
    const badgeElements = ['#totalBadge', '#newBadge', '#contactedBadge', '#followUpBadge'];
    
    statElements.forEach(sel => {
        const el = $(sel);
        if (show) {
            el.textContent = '...';
            el.classList.add('skeleton');
        } else {
            el.classList.remove('skeleton');
        }
    });
    
    badgeElements.forEach(sel => {
        const el = $(sel);
        if (show) {
            el.textContent = '...';
            el.classList.add('skeleton');
        } else {
            el.classList.remove('skeleton');
        }
    });
}

function populateCountyFilter() {
    const counties = [...new Set(allRecords.map(r => r.county).filter(Boolean))].sort();
    const select = $('#countyFilter');
    select.innerHTML = '<option value="">All Counties</option>';
    counties.forEach(c => {
        select.innerHTML += `<option value="${c}">${c}</option>`;
    });
}

function populateRelationshipFilter() {
    // Get unique relationships, normalize case for grouping
    const relationshipMap = new Map();
    allRecords.forEach(r => {
        if (r.executor_relationship) {
            const rel = r.executor_relationship.trim();
            const key = rel.toLowerCase();
            // Keep the most common casing
            if (!relationshipMap.has(key)) {
                relationshipMap.set(key, { display: rel, count: 1 });
            } else {
                relationshipMap.get(key).count++;
            }
        }
    });
    
    // Sort by count descending
    const relationships = [...relationshipMap.entries()]
        .sort((a, b) => b[1].count - a[1].count)
        .map(([key, val]) => val.display);
    
    const select = $('#relationshipFilter');
    select.innerHTML = '<option value="">All Relationships</option>';
    relationships.forEach(r => {
        select.innerHTML += `<option value="${r.toLowerCase()}">${r}</option>`;
    });
}

function updateStats() {
    const stats = {
        total: allRecords.length,
        new: 0,
        contacted: 0,
        followUp: 0,
    };
    
    const today = new Date().toISOString().split('T')[0];
    
    allRecords.forEach(r => {
        if (r.status === 'new') stats.new++;
        if (r.status === 'contacted') stats.contacted++;
        if (r.follow_up_date && r.follow_up_date <= today) stats.followUp++;
    });
    
    animateNumber('#statTotal', stats.total);
    animateNumber('#statNew', stats.new);
    animateNumber('#statContacted', stats.contacted);
    animateNumber('#statFollowUp', stats.followUp);
    
    $('#totalBadge').textContent = stats.total;
    $('#newBadge').textContent = stats.new;
    $('#contactedBadge').textContent = stats.contacted;
    $('#followUpBadge').textContent = stats.followUp;
}

// Animate number counting up
function animateNumber(selector, target) {
    const el = $(selector);
    const duration = 500;
    const start = parseInt(el.textContent) || 0;
    const startTime = performance.now();
    
    el.classList.add('counting');
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(start + (target - start) * eased);
        
        el.textContent = current.toLocaleString();
        
        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            el.classList.remove('counting');
        }
    }
    
    requestAnimationFrame(update);
    
    updateStatusChart();
}

function updateStatusChart() {
    const counts = {};
    allRecords.forEach(r => {
        const s = r.status || 'new';
        counts[s] = (counts[s] || 0) + 1;
    });
    
    const max = Math.max(...Object.values(counts), 1);
    const chart = $('#statusChart');
    
    chart.innerHTML = STATUSES.map(s => {
        const count = counts[s.value] || 0;
        const pct = (count / max) * 100;
        return `
            <div class="status-bar">
                <span class="status-bar-label">${s.label}</span>
                <div class="status-bar-track">
                    <div class="status-bar-fill ${s.value}" style="width: ${pct}%"></div>
                </div>
                <span class="status-bar-count">${count}</span>
            </div>
        `;
    }).join('');
}

// Filtering
function applyFilters() {
    const search = $('#searchInput').value.toLowerCase().trim();
    const county = $('#countyFilter').value;
    const status = $('#statusFilter').value;
    const relationship = $('#relationshipFilter').value;
    const estateValue = $('#estateValueFilter').value;
    const hideApts = $('#hideApartments').checked;
    
    filteredRecords = allRecords.filter(r => {
        // View filter (sidebar navigation)
        if (currentView === 'new' && r.status !== 'new') return false;
        if (currentView === 'contacted' && r.status !== 'contacted') return false;
        if (currentView === 'follow-up') {
            const today = new Date().toISOString().split('T')[0];
            if (!r.follow_up_date || r.follow_up_date > today) return false;
        }
        
        // Status dropdown filter
        if (status && r.status !== status) return false;
        
        // County filter
        if (county && r.county !== county) return false;
        
        // Relationship filter
        if (relationship) {
            const recRel = (r.executor_relationship || '').toLowerCase();
            if (recRel !== relationship) return false;
        }
        
        // Estate value filter
        if (estateValue) {
            const val = parseFloat((r.estimated_estate_value || '').replace(/[^0-9.]/g, '')) || 0;
            if (estateValue === 'hasValue' && val <= 0) return false;
            if (estateValue === 'under10k' && (val <= 0 || val >= 10000)) return false;
            if (estateValue === '10k-30k' && (val < 10000 || val >= 30000)) return false;
            if (estateValue === '30k-50k' && (val < 30000 || val >= 50000)) return false;
            if (estateValue === 'over50k' && val < 50000) return false;
        }
        
        // Search filter
        if (search) {
            const fields = [r.decedent_name, r.executor_name, r.file_number, r.executor_email].map(f => (f || '').toLowerCase());
            if (!fields.some(f => f.includes(search))) return false;
        }
        
        // Hide apartments
        if (hideApts) {
            const addrs = [r.decedent_address, r.executor_address];
            if (addrs.some(a => isApartment(a))) return false;
        }
        
        return true;
    });
    
    sortRecords();
    renderRecords();
}

function isApartment(addr) {
    if (!addr) return false;
    return /\b(apt|apartment|unit|suite|ste|floor|fl|rm|room|#)\b/i.test(addr);
}

function sortRecords() {
    filteredRecords.sort((a, b) => {
        let va = a[sortColumn] || '';
        let vb = b[sortColumn] || '';
        va = va.toString().toLowerCase();
        vb = vb.toString().toLowerCase();
        if (va < vb) return sortDirection === 'asc' ? -1 : 1;
        if (va > vb) return sortDirection === 'asc' ? 1 : -1;
        return 0;
    });
}

// Rendering
function renderRecords() {
    const tbody = $('#recordsBody');
    const container = $('.table-container');
    
    if (filteredRecords.length === 0) {
        container.style.display = 'none';
        $('#noResults').classList.add('show');
        $('#recordCount').textContent = '0 records';
        return;
    }
    
    container.style.display = 'block';
    $('#noResults').classList.remove('show');
    $('#recordCount').textContent = `${filteredRecords.length} records`;
    
    tbody.innerHTML = filteredRecords.map((r, i) => `
        <tr data-index="${i}">
            <td><span class="status-pill ${r.status || 'new'}">${r.status || 'new'}</span></td>
            <td>${esc(r.county) || '—'}</td>
            <td>${esc(r.file_number) || '—'}</td>
            <td>${esc(r.decedent_name) || '—'}</td>
            <td class="address-cell">${esc(r.decedent_address) || '—'}</td>
            <td>${formatDate(r.date_of_death)}</td>
            <td>${esc(r.executor_name) || '—'}</td>
            <td>${r.executor_phone ? `<a href="tel:${r.executor_phone}" class="phone-link">${esc(r.executor_phone)}</a>` : '—'}</td>
            <td>${formatFollowUp(r.follow_up_date)}</td>
            <td><button class="btn-sm" onclick="event.stopPropagation(); openPanel(${i})">View</button></td>
        </tr>
    `).join('');
}

function formatFollowUp(date) {
    if (!date) return '—';
    const today = new Date().toISOString().split('T')[0];
    let cls = '';
    if (date < today) cls = 'overdue';
    else if (date === today) cls = 'today';
    return `<span class="follow-up-date ${cls}">${formatDate(date)}</span>`;
}

function formatDate(str) {
    if (!str) return '—';
    try {
        const d = new Date(str + 'T00:00:00');
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return str;
    }
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Views
function switchView(view) {
    currentView = view;
    
    $$('.nav-item').forEach(n => n.classList.remove('active'));
    $(`.nav-item[data-view="${view}"]`).classList.add('active');
    
    $$('.view').forEach(v => v.classList.remove('active'));
    
    if (view === 'dashboard') {
        $('#dashboardView').classList.add('active');
        $('.topbar').classList.remove('show');
    } else {
        $('#recordsView').classList.add('active');
        $('.topbar').classList.add('show');
        
        // Update title based on view
        const titles = {
            'records': 'All Records',
            'new': 'New Leads',
            'contacted': 'Contacted',
            'follow-up': 'Follow-ups Due'
        };
        $('.view-title', $('#recordsView')).textContent = titles[view] || 'Records';
    }
    
    applyFilters();
}

// Panel
function openPanel(index) {
    currentRecord = filteredRecords[index];
    if (!currentRecord) return;
    
    $('#panelTitle').textContent = currentRecord.decedent_name || 'Record Details';
    
    const content = `
        <div class="panel-section">
            <h4 class="panel-section-title">CRM Status</h4>
            <div class="crm-controls">
                <div class="control-group">
                    <label class="control-label">Status</label>
                    <select class="control-select" id="statusSelect" onchange="updateCRM()">
                        ${STATUSES.map(s => `<option value="${s.value}" ${currentRecord.status === s.value ? 'selected' : ''}>${s.label}</option>`).join('')}
                    </select>
                </div>
                <div class="control-group">
                    <label class="control-label">Follow-up Date</label>
                    <input type="date" class="control-input" id="followUpInput" value="${currentRecord.follow_up_date || ''}" onchange="updateCRM()">
                </div>
                <div class="control-group">
                    <label class="control-label">Priority</label>
                    <select class="control-select" id="prioritySelect" onchange="updateCRM()">
                        <option value="low" ${currentRecord.priority === 'low' ? 'selected' : ''}>Low</option>
                        <option value="normal" ${currentRecord.priority === 'normal' ? 'selected' : ''}>Normal</option>
                        <option value="high" ${currentRecord.priority === 'high' ? 'selected' : ''}>High</option>
                    </select>
                </div>
            </div>
        </div>
        
        <div class="panel-section">
            <h4 class="panel-section-title">Decedent Information</h4>
            <div class="detail-grid">
                <div class="detail-row">
                    <span class="detail-label">Name</span>
                    <span class="detail-value">${esc(currentRecord.decedent_name) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Date of Death</span>
                    <span class="detail-value">${formatDate(currentRecord.date_of_death)}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Address</span>
                    <span class="detail-value">${esc(currentRecord.decedent_address) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">County</span>
                    <span class="detail-value">${esc(currentRecord.county) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">File #</span>
                    <span class="detail-value">${esc(currentRecord.file_number) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Est. Estate Value</span>
                    <span class="detail-value estate-value">${esc(currentRecord.estimated_estate_value) || '—'}</span>
                </div>
            </div>
        </div>
        
        <div class="panel-section">
            <h4 class="panel-section-title">Executor / Administrator</h4>
            <div class="detail-grid">
                <div class="detail-row">
                    <span class="detail-label">Name</span>
                    <span class="detail-value">${esc(currentRecord.executor_name) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Relationship</span>
                    <span class="detail-value">${esc(currentRecord.executor_relationship) || '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Phone</span>
                    <span class="detail-value">${currentRecord.executor_phone ? `<a href="tel:${currentRecord.executor_phone}">${esc(currentRecord.executor_phone)}</a>` : '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Email</span>
                    <span class="detail-value">${currentRecord.executor_email ? `<a href="mailto:${currentRecord.executor_email}">${esc(currentRecord.executor_email)}</a>` : '—'}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Address</span>
                    <span class="detail-value">${esc(currentRecord.executor_address) || '—'}</span>
                </div>
            </div>
        </div>
        
        <div class="panel-section notes-section">
            <h4 class="panel-section-title">Notes & Activity</h4>
            <div class="note-input-group">
                <input type="text" class="note-input" id="noteInput" placeholder="Add a note...">
                <button class="btn-add-note" onclick="addNote()">Add</button>
            </div>
            <div class="notes-list" id="notesList">
                <p class="empty-state">Loading...</p>
            </div>
        </div>
    `;
    
    $('#panelContent').innerHTML = content;
    $('#slidePanel').classList.add('open');
    $('#panelOverlay').classList.add('show');
    
    loadActivities();
}

function closePanel() {
    $('#slidePanel').classList.remove('open');
    $('#panelOverlay').classList.remove('show');
    currentRecord = null;
}

async function updateCRM() {
    if (!currentRecord || !currentRecord.file_number) return;
    
    const data = {
        status: $('#statusSelect').value,
        follow_up_date: $('#followUpInput').value || null,
        priority: $('#prioritySelect').value,
        tags: currentRecord.tags || []
    };
    
    try {
        await fetch(`/api/records/${encodeURIComponent(currentRecord.file_number)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        
        // Update local data
        const idx = allRecords.findIndex(r => r.file_number === currentRecord.file_number);
        if (idx >= 0) {
            Object.assign(allRecords[idx], data);
        }
        Object.assign(currentRecord, data);
        
        applyFilters();
        updateStats();
        
    } catch (error) {
        console.error('Error updating:', error);
    }
}

async function loadActivities() {
    if (!currentRecord || !currentRecord.file_number) return;
    
    try {
        const resp = await fetch(`/api/records/${encodeURIComponent(currentRecord.file_number)}/activities`);
        const activities = await resp.json();
        
        const list = $('#notesList');
        if (activities.length === 0) {
            list.innerHTML = '<p class="empty-state">No notes yet</p>';
            return;
        }
        
        list.innerHTML = activities.map(a => `
            <div class="note-item">
                <div class="note-content">${esc(a.content)}</div>
                <div class="note-meta">${formatDateTime(a.created_at)} • ${a.activity_type}</div>
            </div>
        `).join('');
        
    } catch (error) {
        console.error('Error loading activities:', error);
    }
}

async function addNote() {
    if (!currentRecord || !currentRecord.file_number) return;
    
    const input = $('#noteInput');
    const content = input.value.trim();
    if (!content) return;
    
    try {
        await fetch(`/api/records/${encodeURIComponent(currentRecord.file_number)}/activities`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ activity_type: 'note', content })
        });
        
        input.value = '';
        loadActivities();
        
    } catch (error) {
        console.error('Error adding note:', error);
    }
}

function formatDateTime(str) {
    if (!str) return '';
    try {
        const d = new Date(str);
        return d.toLocaleDateString('en-US', { 
            month: 'short', 
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        });
    } catch {
        return str;
    }
}

// UI Helpers
function showLoading(show) {
    $('#loading').classList.toggle('show', show);
}

// Event Listeners
function setupEventListeners() {
    // Search
    let timeout;
    $('#searchInput').addEventListener('input', () => {
        clearTimeout(timeout);
        timeout = setTimeout(applyFilters, 200);
    });
    
    // Filters
    $('#countyFilter').addEventListener('change', applyFilters);
    $('#statusFilter').addEventListener('change', applyFilters);
    $('#relationshipFilter').addEventListener('change', applyFilters);
    $('#estateValueFilter').addEventListener('change', applyFilters);
    $('#hideApartments').addEventListener('change', applyFilters);
    
    // Refresh
    $('#refreshBtn').addEventListener('click', loadRecords);
    
    // Theme
    $('#themeToggle').addEventListener('click', toggleTheme);
    
    // Navigation
    $$('.nav-item').forEach(n => {
        n.addEventListener('click', (e) => {
            e.preventDefault();
            switchView(n.dataset.view);
        });
    });
    
    // Clickable stat cards (for mobile navigation)
    $$('.stat-card.clickable').forEach(card => {
        card.addEventListener('click', () => {
            const view = card.dataset.view;
            if (view) {
                switchView(view);
            }
        });
    });
    
    // Table sorting
    $$('.records-table th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.sort;
            if (sortColumn === col) {
                sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                sortColumn = col;
                sortDirection = 'asc';
            }
            applyFilters();
        });
    });
    
    // Row click
    $('#recordsBody').addEventListener('click', (e) => {
        const row = e.target.closest('tr');
        if (row && !e.target.closest('button') && !e.target.closest('a')) {
            openPanel(parseInt(row.dataset.index));
        }
    });
    
    // Panel close
    $('#panelClose').addEventListener('click', closePanel);
    $('#panelOverlay').addEventListener('click', closePanel);
    
    // Keyboard
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePanel();
        if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
            e.preventDefault();
            $('#searchInput').focus();
        }
    });
    
    // Enter key for notes
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.target.id === 'noteInput') {
            addNote();
        }
    });
}
