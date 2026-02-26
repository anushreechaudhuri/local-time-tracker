// Edit entry
async function editEntry(id) {
    const resp = await fetch(`/api/entries/${id}`);
    // We need to fetch entry details — for now reconstruct from DOM + a lightweight endpoint
    // Open modal with current values from the card
    const card = document.querySelector(`.entry-card[data-id="${id}"]`);
    if (!card) return;

    document.getElementById('edit-id').value = id;
    document.getElementById('edit-desc').value = card.querySelector('.entry-desc').textContent.trim();

    // Try to set project dropdown
    const projName = card.querySelector('.entry-meta span:nth-child(2)').textContent.trim();
    const projSelect = document.getElementById('edit-project');
    for (const opt of projSelect.options) {
        if (opt.text === projName) { projSelect.value = opt.value; break; }
    }

    // Parse times from the card
    const times = card.querySelectorAll('.entry-time span:not(.time-sep)');
    if (times.length >= 2) {
        const dateStr = new URLSearchParams(window.location.search).get('date') || new Date().toISOString().slice(0, 10);
        const startTime = times[0].textContent.trim();
        const endTime = times[1].textContent.trim();
        if (startTime !== 'now') {
            document.getElementById('edit-start').value = `${dateStr}T${startTime}`;
        }
        if (endTime !== 'now') {
            document.getElementById('edit-end').value = `${dateStr}T${endTime}`;
        }
    }

    document.getElementById('edit-modal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('edit-modal').classList.add('hidden');
}

document.getElementById('edit-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('edit-id').value;
    const tagCheckboxes = document.querySelectorAll('#edit-tags input:checked');
    const tagIds = Array.from(tagCheckboxes).map(cb => parseInt(cb.value));

    const startVal = document.getElementById('edit-start').value;
    const endVal = document.getElementById('edit-end').value;

    const data = {
        description: document.getElementById('edit-desc').value,
        project_id: parseInt(document.getElementById('edit-project').value),
        tag_ids: tagIds,
    };
    if (startVal) {
        // datetime-local gives "YYYY-MM-DDTHH:MM", we need "YYYY-MM-DD HH:MM:SS"
        data.start_time = startVal.replace('T', ' ');
        if (data.start_time.length === 16) data.start_time += ':00';
    }
    if (endVal) {
        data.end_time = endVal.replace('T', ' ');
        if (data.end_time.length === 16) data.end_time += ':00';
    }

    const resp = await fetch(`/api/entries/${id}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
    });
    if (resp.ok) {
        closeModal();
        location.reload();
    } else {
        console.error('Save failed:', await resp.text());
    }
});

async function deleteEntry(id) {
    if (!confirm('Delete this entry?')) return;
    await fetch(`/api/entries/${id}`, { method: 'DELETE' });
    location.reload();
}

// Settings page: add project/tag
async function addProject(e) {
    e.preventDefault();
    const name = document.getElementById('new-project-name').value;
    const color = document.getElementById('new-project-color').value;
    await fetch('/api/projects', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, color }),
    });
    location.reload();
}

async function addTag(e) {
    e.preventDefault();
    const name = document.getElementById('new-tag-name').value;
    const color = document.getElementById('new-tag-color').value;
    await fetch('/api/tags', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, color }),
    });
    location.reload();
}

// Close modal on backdrop click
document.getElementById('edit-modal')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
});

// Close modal on Escape
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
});
